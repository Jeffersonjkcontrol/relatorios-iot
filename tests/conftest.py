"""Configuração dos testes.

Os bancos do app são abertos na importação, a partir de DATA_DIR. Por isso o
DATA_DIR de teste é definido AQUI, antes de qualquer import de `app` — assim os
testes nunca tocam no banco real do desenvolvedor.
"""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_TMP = tempfile.mkdtemp(prefix="mes_testes_")
os.environ["DATA_DIR"] = _TMP
os.environ["MES_COLETOR"] = "0"          # nenhum teste fala com a plataforma IoT
os.environ.setdefault("MES_TZ", "America/Sao_Paulo")
os.environ["SESSION_SECRET"] = "segredo-de-teste"

import pytest  # noqa: E402


@pytest.fixture()
def banco(tmp_path):
    """Bancos novos e vazios para cada teste: o do MES e o da legenda RFID."""
    import app.ai.db as banco_geral
    import app.cadastros as legenda
    from app.mes import db
    db.usar_banco(tmp_path / "mes.db")
    db.garantir_schema()
    original = banco_geral.DB_PATH
    banco_geral.DB_PATH = tmp_path / "ai_history.db"
    legenda._init()
    import app.admin.settings as ajustes
    import app.auth.db as usuarios
    usuarios._init()
    ajustes._init()
    yield db
    banco_geral.DB_PATH = original
    db.usar_banco(Path(_TMP) / "mes.db")


@pytest.fixture()
def maq(banco):
    """Duas injetoras cadastradas; devolve a primeira."""
    from app.mes import cadastros
    cadastros.sincronizar_maquinas("ubidots", [
        {"label": "inj01", "name": "Injetora 01"}, {"label": "inj02", "name": "Injetora 02"}])
    return cadastros.obter_maquina_por_codigo("inj01")
