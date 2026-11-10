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
    """Banco do MES novo e vazio para cada teste."""
    from app.mes import db
    db.usar_banco(tmp_path / "mes.db")
    db.garantir_schema()
    yield db
    db.usar_banco(Path(_TMP) / "mes.db")
