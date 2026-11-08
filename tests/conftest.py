import os

# Definido antes de importar a aplicação: o JWT_SECRET é obrigatório e não tem
# valor padrão no código.
os.environ.setdefault("JWT_SECRET", "segredo-de-teste-com-mais-de-32-bytes-de-tamanho")

import uuid

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient

from app.domain.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.infrastructure.mongo.database import create_client, ensure_indexes, unit_of_work_factory
from app.infrastructure.security.jwt_tokens import Principal, issue_token
from app.interfaces.http.dependencies import get_current_principal
from app.main import API_V1, create_app

MONGODB_URL = os.getenv("TEST_MONGODB_URL")

# CPF válido pelos dígitos verificadores, usado como o cliente autenticado.
AUTH_CPF = "11144477735"


def pytest_collection_modifyitems(items):
    """Marca cada teste conforme a pasta, para rodar `pytest -m unit` ou `-m integration`."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)


def _rotas_de_teste() -> APIRouter:
    """Rotas que existem só nos testes, para exercitar a infraestrutura comum
    (autenticação, métricas por template de rota, tratamento de exceções)."""
    router = APIRouter(prefix="/_test")

    @router.get("/protected")
    async def protected(principal: Principal = Depends(get_current_principal)):
        return {"subject": principal.subject, "client_id": principal.client_id}

    @router.get("/items/{item_id}")
    async def item(item_id: int):
        return {"id": item_id}

    @router.get("/not-found")
    async def not_found():
        raise NotFoundError("Item", 42)

    @router.get("/conflict")
    async def conflict():
        raise ConflictError("Item duplicado")

    @router.get("/business-rule")
    async def business_rule():
        raise BusinessRuleError("Transição inválida")

    @router.get("/boom")
    async def boom():
        raise RuntimeError("falha inesperada")

    return router


@pytest.fixture
def app():
    application = create_app()
    application.include_router(_rotas_de_teste(), prefix=API_V1)
    return application


@pytest_asyncio.fixture
async def mongo():
    """Banco MongoDB descartável por teste. Sem `TEST_MONGODB_URL`, o teste é pulado."""
    if not MONGODB_URL:
        pytest.skip("TEST_MONGODB_URL não definida")
    client = create_client(MONGODB_URL)
    db = client[f"autogiro_execution_test_{uuid.uuid4().hex[:10]}"]
    await ensure_indexes(db)
    yield client, db
    await client.drop_database(db.name)
    await client.close()


@pytest.fixture
def db(mongo):
    return mongo[1]


@pytest.fixture
def uow_factory(mongo):
    return unit_of_work_factory(*mongo)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def token() -> str:
    return issue_token(AUTH_CPF, client_id=1, name="Cliente de Teste")


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_client(app, uow_factory, auth_headers):
    """Cliente HTTP autenticado, com a API ligada ao banco descartável do teste."""
    app.state.uow_factory = uow_factory
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=auth_headers) as c:
        yield c
