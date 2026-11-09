# AutoGiro · Execution Service

[![CI/CD](https://github.com/Figueiraa/autogiro-execution-service/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/Figueiraa/autogiro-execution-service/actions/workflows/ci-cd.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=alert_status)](https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-execution-service)
[![Cobertura](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=coverage)](https://sonarcloud.io/component_measures?id=Figueiraa_autogiro-execution-service&metric=coverage)

> Microsserviço de **Produção** da plataforma AutoGiro, para redes de oficinas mecânicas.
> Tech Challenge **Fase 4** (13SOAT): arquitetura de microsserviços com Saga Pattern.

| | |
|---|---|
| **É dono de** | Peças e estoque, reservas, fila de execução, diagnóstico e reparo |
| **Papel na Saga** | Participante: reserva e libera peças, enfileira e executa |
| **Banco** | MongoDB (Atlas) |
| **Comunicação** | REST pelo Kong (borda) · RabbitMQ (comandos e eventos da Saga) |

```mermaid
flowchart LR
    MEC([Mecânico]) -->|REST + JWT| KONG[Kong] --> API[Execution Service]
    OS[OS Service] -->|"execution.commands"| API
    OS -.->|"GET /api/v1/parts/{id}"| API
    API -->|"autogiro.events (execution.*)"| OS
    API --> DB[(MongoDB)]
```

## Documentação

- [Arquitetura do serviço](docs/arquitetura.md): componentes, coleções e índices do MongoDB,
  máquinas de estado da reserva e da execução, mensagens, capacidade da fila, rotas do
  mecânico e rastreamento.
- [Documentação geral da plataforma](https://github.com/Figueiraa/autogiro-os-service/blob/main/docs/README.md)
  (no OS Service): visão geral, Saga e decisões compartilhadas.

## Estrutura

Clean Architecture (domínio, aplicação, infraestrutura e interfaces), sondas de saúde,
métricas Prometheus, logs JSON, validação do JWT emitido pelo `autogiro-auth` e pipeline com
testes, SonarCloud e imagem Docker.

## API

Todas as rotas exigem o JWT do `autogiro-auth`. Documentação interativa em `/docs` (Swagger).

| Método | Rota | Descrição |
|---|---|---|
| `GET` `POST` | `/api/v1/parts` | Listar e cadastrar peças |
| `GET` `PATCH` `DELETE` | `/api/v1/parts/{id}` | Detalhar (com o saldo disponível), alterar e remover (recusado se a peça tem unidades reservadas). O detalhe também é consultado pelo OS Service na abertura da OS |
| `POST` | `/api/v1/parts/{id}/stock` | Entrada ou baixa manual de estoque |
| `GET` | `/api/v1/executions` | Execuções por ordem de chegada; `?status=NA_FILA` devolve a fila da oficina |
| `GET` | `/api/v1/executions/{order_id}` | Execução da OS, com a linha do tempo |
| `POST` | `/api/v1/executions/{order_id}/diagnosis` | `NA_FILA → EM_DIAGNOSTICO` |
| `POST` | `/api/v1/executions/{order_id}/repair` | `EM_DIAGNOSTICO → EM_REPARO` |
| `POST` | `/api/v1/executions/{order_id}/finish` | `EM_REPARO → FINALIZADA` (baixa as peças reservadas do estoque) |
| `POST` | `/api/v1/executions/{order_id}/cancel` | Cancela, com motivo (de `NA_FILA`, `EM_DIAGNOSTICO` ou `EM_REPARO`): dispara as compensações da Saga |

## Estoque e reservas

Cada peça tem `stock_quantity` (o que existe) e `reserved_quantity` (o que está prometido a
ordens de serviço ainda não executadas). O **disponível** é a diferença.

- **Reserva tudo ou nada:** se uma peça da OS falta, nenhuma é reservada.
- **Sem venda dupla:** a reserva é uma atualização condicional e atômica no MongoDB
  (só incrementa o reservado se o saldo ainda comportar). Duas OS disputando a última peça
  nunca levam as duas.
- **Liberação:** a compensação devolve o reservado ao disponível.
- **Consumo:** ao finalizar o reparo, o reservado sai do estoque.

## Participação na Saga

O orquestrador fica no OS Service. Este serviço recebe comandos na fila
`execution.commands` (routing keys `execution.reservar-pecas`, `execution.liberar-pecas` e
`execution.enfileirar-execucao`, com as filas `execution.commands.retry` e
`execution.commands.dlq`) e responde com eventos no exchange `autogiro.events`, com routing key
`execution.<evento-em-kebab-case>`:

| Comando | Resposta |
|---|---|
| `ReservarPecas` | `PecasReservadas` (com nome e preço) ou `PecasIndisponiveis` (o que faltou e por quê) |
| `LiberarPecas` (compensação) | `PecasLiberadas` |
| `EnfileirarExecucao` | `ExecucaoEnfileirada` (com a posição) ou `EnfileiramentoRecusado` (fila cheia) |

O mecânico gera os demais eventos pela API: `DiagnosticoIniciado`, `ReparoIniciado`,
`ExecucaoFinalizada` e `ExecucaoCancelada`.

- **Idempotência:** reserva e execução são únicas por OS. Um comando reenviado não refaz o
  efeito, só responde de novo. A mesma mensagem entregue duas vezes é descartada.
- **Outbox:** cada mudança e o evento de resposta são gravados na mesma transação do MongoDB
  e publicados depois. Nenhum evento se perde se o RabbitMQ estiver fora.
- **Fila da oficina:** no máximo `EXECUTION_QUEUE_CAPACITY` OS aguardando (padrão 20).
  Acima disso, a OS é recusada e a Saga desfaz o que foi feito, inclusive o estorno do pagamento.

## Observabilidade

Métricas Prometheus em `GET /metrics` (raspadas pelo agente do New Relic no cluster) e
**rastreamento distribuído com OpenTelemetry**, exportado direto para o New Relic por OTLP.

| Variável | Uso |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://otlp.nr-data.net`. Sem ela, nada é exportado e o serviço funciona igual |
| `NEW_RELIC_LICENSE_KEY` | License key de ingestão, enviada no cabeçalho `api-key` |
| `DEPLOYMENT_ENVIRONMENT` | Ambiente nos traces (`local`, `producao`) |

Como uma OS vira **um trace só** entre os serviços:

- HTTP de entrada (FastAPI) e o MongoDB (pymongo) são instrumentados automaticamente. O serviço
  não faz chamadas HTTP de saída; a consulta ao catálogo feita pelo OS Service chega com o
  cabeçalho `traceparent` (W3C) e continua o trace da abertura da OS.
- Cada mensagem gravada no outbox guarda o contexto do trace em que nasceu. O publicador envia esse
  contexto nos cabeçalhos AMQP e o consumidor continua o mesmo trace. Assim o trace segue o fluxo
  de negócio, e não o laço que publica o outbox.
- Spans de mensagem levam `autogiro.saga_id`, `autogiro.order_id`, `autogiro.message.type` e o
  desfecho da entrega (`done`, `retry` ou `dead_letter`).
- Os logs JSON levam `trace.id` e `span.id` (nomes que o New Relic usa para ligar log e trace) e,
  durante o processamento de uma mensagem, `saga_id`, `order_id` e `message_type`.
- Consultas ao banco feitas pelos laços em segundo plano (outbox, sondas de saúde) não abrem
  trace próprio: só aparecem dentro de uma requisição ou mensagem.

O painel **AutoGiro — Saga e rastreamento** e o alerta de mensagem na DLQ ficam no
[autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s), em Terraform.

## Banco de dados

MongoDB, porque a execução é um documento que cresce: a linha do tempo de diagnóstico e
reparo tem tamanho e conteúdo variáveis. A reserva de várias peças usa **transação
multi-documento**, que exige replica set: o Atlas M0 já é um, e localmente o Mongo sobe com
`--replSet rs0`.

| Coleção | Conteúdo |
|---|---|
| `parts` | Peças, estoque e quantidade reservada |
| `reservations` | Uma reserva por OS (`RESERVADA`, `LIBERADA` ou `CONSUMIDA`) |
| `executions` | Uma execução por OS, com a linha do tempo |
| `outbox` | Eventos a publicar |
| `processed_messages` | Comandos já processados |
| `counters` | Sequência dos ids numéricos das peças |

Em produção o banco fica num cluster **MongoDB Atlas M0** (plano gratuito), fora do
Kubernetes, para sobreviver à recriação do cluster. A string de conexão chega ao pod por
Secret (`MONGODB_URL`). `python -m app.infrastructure.mongo.seed` cria os índices e o estoque
de demonstração (8 peças, sem sobrescrever o que já existe); a API também garante os índices ao
subir.

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

pytest                                  # testes + cobertura (mínimo de 80%)
ruff check app tests scripts            # lint
bandit -r app -ll                       # análise de segurança

cp .env.example .env                    # defina o JWT_SECRET
docker compose up --build               # estoque de demonstração + API em http://localhost:8003
```

Os testes de integração usam MongoDB e RabbitMQ reais e rodam quando `TEST_MONGODB_URL` e
`TEST_RABBITMQ_URL` estão definidas (no CI, containers dos dois). Cada teste usa um banco
descartável:

```bash
docker compose up -d mongo rabbitmq
TEST_MONGODB_URL="mongodb://localhost:27017/?replicaSet=rs0&directConnection=true" \
TEST_RABBITMQ_URL=amqp://guest:guest@localhost:5674/ pytest
```

O `JWT_SECRET` é obrigatório e não tem valor padrão no código: o serviço não sobe sem ele.

## Kubernetes

Manifestos em [`k8s/`](k8s/), aplicados com `kubectl apply -k k8s/` no namespace `autogiro` do
cluster EKS criado pelo [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s).

| Recurso | Configuração |
|---|---|
| Deployment | 2 réplicas, usuário sem privilégios, sistema de arquivos somente leitura, sondas em `/health/live` e `/health/ready`. Índices e catálogo inicial de peças criados por um *init container* (idempotente). |
| Service | ClusterIP na porta 80 |
| HPA | 2 a 6 réplicas, por CPU (70%) e memória (80%) |
| Ingress (Kong) | `/api/v1/parts` e `/api/v1/executions`, com JWT. |
| ConfigMap | Configuração própria do serviço |

A configuração da plataforma vem do Secret e do ConfigMap `autogiro-plataforma`, criados pelo
`autogiro-infra-k8s`: `JWT_SECRET`, `RABBITMQ_URL` (broker no cluster), `NEW_RELIC_LICENSE_KEY` e o
endpoint OTLP. O Secret próprio do serviço, `execution-service-secret`, tem a forma de
[`k8s/secret.example.yaml`](k8s/secret.example.yaml):

| Chave | Conteúdo |
|---|---|
| `MONGODB_URL` | Cluster do MongoDB Atlas (M0) |

## Pipeline (`.github/workflows/ci-cd.yml`)

| Job | O que faz | Bloqueia o merge? |
|---|---|---|
| **Lint & Test** | ruff, bandit e pytest com cobertura mínima de 80% | Sim |
| **SonarCloud** | Análise de qualidade com quality gate bloqueante | Sim |
| **Build & Push image** | Imagem Docker + scan de vulnerabilidades (trivy). Na `main`, publica no GHCR para amd64 e arm64 | Sim |
| **Deploy (EKS)** | Na `main`, com o cluster no ar: atualiza o Secret do serviço, aplica `k8s/` com a imagem do commit, espera o rollout e roda o smoke test | Não (só existe na `main`) |

A `main` é protegida: só recebe código por pull request, com as três checagens verdes.

### Deploy contínuo

Todo merge na `main` publica o serviço no EKS, sem passo manual:

1. A imagem sai com a tag do commit (imutável); o `k8s/kustomization.yaml` passa a apontar para ela.
2. O Secret `execution-service-secret` é atualizado a partir dos secrets do GitHub (`MONGODB_URL`).
3. `kubectl apply -k k8s/` e `kubectl rollout status`. Se o rollout não terminar em 5 minutos, a
   pipeline faz `rollout undo` e falha: a versão anterior continua atendendo.
4. Smoke test: o Deployment roda a imagem do commit com todas as réplicas disponíveis, a prontidão
   (banco e broker) responde de dentro do pod e a rota está publicada no Kong (401 sem token).
5. O resumo da execução registra commit, imagem, revisão do Deployment e os pods em execução.

O job só roda com a variável `DEPLOY_ENABLED` do repositório em `true`. Ela é ligada pelo script que
sobe o cluster, que também dispara a pipeline da `main` (`workflow_dispatch`) para a primeira
publicação, e desligada pelo script que o destrói: com o cluster desligado o job aparece como
*skipped* e a pipeline segue verde. As credenciais da AWS (`AWS_ACCESS_KEY_ID` e
`AWS_SECRET_ACCESS_KEY`) também ficam nos secrets do repositório.

## Qualidade e cobertura

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=alert_status)](https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-execution-service)
[![Cobertura](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=coverage)](https://sonarcloud.io/component_measures?id=Figueiraa_autogiro-execution-service&metric=coverage)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=bugs)](https://sonarcloud.io/project/issues?id=Figueiraa_autogiro-execution-service&types=BUG)
[![Vulnerabilidades](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=vulnerabilities)](https://sonarcloud.io/project/issues?id=Figueiraa_autogiro-execution-service&types=VULNERABILITY)
[![Code smells](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-execution-service&metric=code_smells)](https://sonarcloud.io/project/issues?id=Figueiraa_autogiro-execution-service&types=CODE_SMELL)

Cobertura de **98,7%** na `main`, com 139 testes, quality gate aprovado e nenhum bug,
vulnerabilidade ou code smell aberto. A pipeline exige no mínimo 80% (`--cov-fail-under=80`) e o
quality gate do SonarCloud bloqueia o merge.

![Painel do SonarCloud](docs/img/sonarcloud.png)

- **Painel do SonarCloud:** <https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-execution-service> (os badges acima são atualizados a cada análise).
- **Relatório de cada execução:** aba *Summary* da pipeline no GitHub Actions, com o quadro de testes e cobertura, e o artefato `test-and-coverage-reports` (JUnit, `coverage.xml` e HTML).

## Swagger e Postman

- **Swagger:** `http://localhost:8003/docs` com o serviço rodando localmente; no cluster,
  `kubectl -n autogiro port-forward svc/execution-service 8003:80` e o mesmo endereço.
- [`docs/openapi.json`](docs/openapi.json): a especificação OpenAPI exportada da aplicação.
- [`docs/postman/execution-service.postman_collection.json`](docs/postman/execution-service.postman_collection.json): todas as rotas do serviço,
  com as variáveis `base_url`, `token` e os ids usados nos caminhos.

## Repositórios da plataforma

| Repositório | Papel |
|---|---|
| [autogiro-os-service](https://github.com/Figueiraa/autogiro-os-service) | Atendimento e orquestração da Saga |
| [autogiro-billing-service](https://github.com/Figueiraa/autogiro-billing-service) | Orçamento, pagamento (Mercado Pago) e estorno |
| [autogiro-execution-service](https://github.com/Figueiraa/autogiro-execution-service) | Estoque de peças e fila de execução |
| [autogiro-auth](https://github.com/Figueiraa/autogiro-auth) | Lambda de autenticação por CPF |
| [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s) | Cluster EKS, Kong, RabbitMQ e New Relic |
