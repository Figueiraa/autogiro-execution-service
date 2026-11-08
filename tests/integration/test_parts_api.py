"""Cadastro e estoque de peças pela API, sobre MongoDB."""


async def _criar(auth_client, **campos) -> dict:
    payload = {"name": "Filtro de óleo", "unit_price": 32.5, "stock_quantity": 10, **campos}
    r = await auth_client.post("/api/v1/parts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


class TestCadastro:
    async def test_cria_lista_e_detalha(self, auth_client):
        filtro = await _criar(auth_client)
        oleo = await _criar(auth_client, name="Óleo 5W30", unit="L")

        assert (filtro["id"], oleo["id"]) == (1, 2)
        assert filtro["available"] == 10
        nomes = [p["name"] for p in (await auth_client.get("/api/v1/parts")).json()]
        assert nomes == ["Filtro de óleo", "Óleo 5W30"]
        assert (await auth_client.get(f"/api/v1/parts/{oleo['id']}")).json()["unit"] == "L"

    async def test_atualiza(self, auth_client):
        filtro = await _criar(auth_client)

        r = await auth_client.patch(f"/api/v1/parts/{filtro['id']}", json={"unit_price": 35.0, "description": "Linha leve"})

        assert (r.json()["unit_price"], r.json()["description"], r.json()["name"]) == (35.0, "Linha leve", "Filtro de óleo")

    async def test_remove(self, auth_client):
        filtro = await _criar(auth_client)

        assert (await auth_client.delete(f"/api/v1/parts/{filtro['id']}")).status_code == 204
        assert (await auth_client.get(f"/api/v1/parts/{filtro['id']}")).status_code == 404

    async def test_valida_os_dados(self, auth_client):
        assert (await auth_client.post("/api/v1/parts", json={"name": "", "unit_price": 1})).status_code == 422
        assert (await auth_client.post("/api/v1/parts", json={"name": "X", "unit_price": -1})).status_code == 422
        r = await auth_client.post("/api/v1/parts", json={"name": "X", "unit_price": 1, "stock_quantity": -5})
        assert r.status_code == 422

    async def test_peca_inexistente(self, auth_client):
        assert (await auth_client.get("/api/v1/parts/99")).status_code == 404
        assert (await auth_client.patch("/api/v1/parts/99", json={"name": "X"})).status_code == 404
        assert (await auth_client.delete("/api/v1/parts/99")).status_code == 404
        assert (await auth_client.post("/api/v1/parts/99/stock", json={"quantity": 1})).status_code == 404

    async def test_exige_autenticacao(self, client):
        assert (await client.get("/api/v1/parts")).status_code == 401


class TestEstoque:
    async def test_entrada_e_baixa(self, auth_client):
        filtro = await _criar(auth_client)

        entrada = await auth_client.post(f"/api/v1/parts/{filtro['id']}/stock", json={"quantity": 5})
        baixa = await auth_client.post(f"/api/v1/parts/{filtro['id']}/stock", json={"quantity": -3})

        assert (entrada.json()["stock_quantity"], baixa.json()["stock_quantity"]) == (15, 12)

    async def test_baixa_abaixo_do_reservado_e_recusada(self, auth_client, db):
        filtro = await _criar(auth_client)
        await db["parts"].update_one({"_id": filtro["id"]}, {"$set": {"reserved_quantity": 8}})

        r = await auth_client.post(f"/api/v1/parts/{filtro['id']}/stock", json={"quantity": -5})

        assert r.status_code == 422
        assert "reservadas" in r.json()["detail"]

    async def test_peca_reservada_nao_pode_ser_removida(self, auth_client, db):
        filtro = await _criar(auth_client)
        await db["parts"].update_one({"_id": filtro["id"]}, {"$set": {"reserved_quantity": 1}})

        r = await auth_client.delete(f"/api/v1/parts/{filtro['id']}")

        assert r.status_code == 422
