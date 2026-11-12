"""Testes do agente que rodam sem o app (usados no build do .exe)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agente  # noqa: E402


def test_codificacao_e_blocos():
    assert agente.codificar("38") == list(b"38" + b" " * 14)
    for proibido in (0, 3, 7):
        try:
            agente.validar_bloco(proibido)
            raise AssertionError("aceitou bloco de chave")
        except agente.ErroCartao:
            pass


def test_origem():
    assert agente.origem_permitida("http://relatorios.local")
    assert not agente.origem_permitida("https://exemplo.com")


def test_pyscard_importa():
    import smartcard.System  # noqa: F401  — garante que o .exe leva o pyscard
