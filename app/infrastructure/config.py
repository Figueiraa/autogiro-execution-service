from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SERVICE_NAME: str = "execution-service"
    SERVICE_VERSION: str = "0.1.0"

    # Segredo HS256 compartilhado com a Lambda `autogiro-auth` (emissor) e com o
    # Kong (validador). O serviço revalida a assinatura em vez de confiar no
    # gateway: chamadas internas e a execução local não passam pelo Kong (ADR-004).
    # Obrigatório e sem valor padrão: o serviço não sobe sem ele, e nenhum segredo
    # fica escrito no código.
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "autogiro-auth"

    # Banco próprio do serviço: MongoDB em replica set (transações). Em produção,
    # o Atlas M0; localmente, o Mongo do docker-compose.
    MONGODB_URL: str = "mongodb://localhost:27017/?replicaSet=rs0"
    MONGODB_DATABASE: str = "autogiro_execution"

    # Regra de negócio: quantas OS cabem na fila da oficina ao mesmo tempo.
    EXECUTION_QUEUE_CAPACITY: int = 20

    # Mensageria (RabbitMQ). Sem URL, a API sobe sem consumir comandos nem publicar
    # eventos: eles ficam no outbox até o broker ser configurado.
    RABBITMQ_URL: str | None = None
    OUTBOX_POLL_SECONDS: float = 1.0
    MESSAGE_MAX_RETRIES: int = 3
    MESSAGE_RETRY_DELAY_MS: int = 5000

    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"  # lista separada por vírgula; use origens explícitas em produção

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
