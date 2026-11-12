"""Agente RFID JKControl — ponte entre a página do app e o leitor ACR122U.

Roda no PC onde o leitor está plugado e escuta SÓ em 127.0.0.1. A página do app
(aberta no navegador desse mesmo PC) pede para ler ou gravar; o agente fala com
o leitor pelo driver de cartão do Windows (PC/SC).

Ele não sabe nada de OP ou funcionário: recebe o texto de cada bloco já montado
pelo app, grava, lê de volta e confere. Recusa os blocos de chaves do cartão.

Rotas (JSON):
    GET  /status   leitor e cartão presentes, número de série do cartão
    POST /ler      {"blocos": [1, 2, 4, 5]}           -> {"blocos": {"1": "...", ...}, "uid": "..."}
    POST /gravar   {"blocos": {"1": "fun_rfid", ...}}  -> {"ok": true, "uid": "...", "conferido": true}

Só aceita pedidos de páginas da rede local (http://relatorios.local, IPs
192.168.x.x / 10.x / 172.16-31.x, localhost): um site da internet aberto no
mesmo navegador não consegue gravar cartões.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSAO = "1.0.0"
PORTA = 47321
BLOCOS_PERMITIDOS = {1, 2, 4, 5}
TAMANHO = 16
CHAVE_PADRAO = [0xFF] * 6

log = logging.getLogger("agente_rfid")


class ErroCartao(Exception):
    pass


# ------------------------------------------------------------ comandos ACR122U
def apdu_uid() -> list[int]:
    return [0xFF, 0xCA, 0x00, 0x00, 0x00]


def apdu_carregar_chave(chave=CHAVE_PADRAO) -> list[int]:
    return [0xFF, 0x82, 0x00, 0x00, 0x06, *chave]


def apdu_autenticar(bloco: int) -> list[int]:
    # chave A (0x60) carregada no slot 0
    return [0xFF, 0x86, 0x00, 0x00, 0x05, 0x01, 0x00, bloco, 0x60, 0x00]


def apdu_ler(bloco: int) -> list[int]:
    return [0xFF, 0xB0, 0x00, bloco, TAMANHO]


def apdu_gravar(bloco: int, texto: str) -> list[int]:
    return [0xFF, 0xD6, 0x00, bloco, TAMANHO, *codificar(texto)]


def codificar(texto: str) -> list[int]:
    """16 bytes ASCII completados com espaço (o firmware descarta os espaços)."""
    try:
        dados = str(texto).encode("ascii")
    except UnicodeEncodeError:
        raise ErroCartao(f"'{texto}' tem acento ou caractere especial; o cartão aceita só letras sem acento e números.")
    if len(dados) > TAMANHO:
        raise ErroCartao(f"Texto com {len(dados)} bytes; o bloco comporta {TAMANHO}.")
    return list(dados.ljust(TAMANHO, b" "))


def validar_bloco(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ErroCartao(f"Bloco inválido: {n!r}")
    if n not in BLOCOS_PERMITIDOS:
        raise ErroCartao(f"Bloco {n} não é permitido (só {sorted(BLOCOS_PERMITIDOS)}): "
                         "os blocos 0, 3, 7... protegem o cartão.")
    return n


# ------------------------------------------------------------------ leitor
class Leitor:
    """Operações num cartão. `transmitir(apdu) -> (dados, sw1, sw2)` vem do pyscard
    (ou de um leitor simulado nos testes)."""

    def __init__(self, transmitir):
        self.transmitir = transmitir

    def _ok(self, apdu, oque: str) -> list[int]:
        dados, sw1, sw2 = self.transmitir(apdu)
        if (sw1, sw2) != (0x90, 0x00):
            raise ErroCartao(f"Falha ao {oque} (resposta {sw1:02X}{sw2:02X}). "
                             "Mantenha o cartão parado sobre o leitor e tente de novo.")
        return dados

    def uid(self) -> str:
        return "".join(f"{b:02X}" for b in self._ok(apdu_uid(), "ler o número do cartão"))

    def _autenticar(self, bloco: int) -> None:
        self._ok(apdu_autenticar(bloco), f"autenticar o bloco {bloco} (cartão de outro tipo ou com senha?)")

    def ler(self, blocos) -> dict[str, str]:
        self._ok(apdu_carregar_chave(), "carregar a chave")
        out = {}
        for n in blocos:
            n = validar_bloco(n)
            self._autenticar(n)
            dados = self._ok(apdu_ler(n), f"ler o bloco {n}")
            out[str(n)] = bytes(dados).decode("ascii", errors="replace").rstrip(" \x00")
        return out

    def gravar(self, blocos: dict) -> dict:
        pedidos = {validar_bloco(n): str(t) for n, t in blocos.items()}
        for texto in pedidos.values():
            codificar(texto)                      # valida tudo antes de gravar qualquer coisa
        uid = self.uid()
        self._ok(apdu_carregar_chave(), "carregar a chave")
        for n, texto in sorted(pedidos.items()):
            self._autenticar(n)
            self._ok(apdu_gravar(n, texto), f"gravar o bloco {n}")
        lido = self.ler(sorted(pedidos))
        diferentes = [n for n, t in pedidos.items() if lido[str(n)] != t.rstrip(" ")]
        if diferentes:
            raise ErroCartao(f"O cartão não confirmou a gravação dos blocos {diferentes}. Grave de novo.")
        return {"ok": True, "uid": uid, "conferido": True, "blocos": lido}


def abrir_leitor():
    """Conecta no primeiro leitor PC/SC com cartão. Devolve (leitor, conexão, nome)."""
    from smartcard.Exceptions import CardConnectionException, NoCardException
    from smartcard.System import readers

    lista = readers()
    if not lista:
        raise ErroCartao("SEM_LEITOR")
    nome = str(lista[0])
    conexao = lista[0].createConnection()
    try:
        conexao.connect()
    except (NoCardException, CardConnectionException):
        raise ErroCartao("SEM_CARTAO:" + nome)
    return Leitor(conexao.transmit), conexao, nome


# ----------------------------------------------------------------- origem
def origem_permitida(origem: str | None) -> bool:
    """Só páginas da rede local podem usar o agente."""
    if not origem:
        return False
    host = (urlparse(origem).hostname or "").lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


# ------------------------------------------------------------- servidor HTTP
_trava = threading.Lock()          # um cartão por vez


class Handler(BaseHTTPRequestHandler):
    server_version = f"AgenteRFID/{VERSAO}"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _cors(self) -> bool:
        origem = self.headers.get("Origin")
        if origem and not origem_permitida(origem):
            return False
        if origem:
            self.send_header("Access-Control-Allow-Origin", origem)
            self.send_header("Vary", "Origin")
        return True

    def _responder(self, status: int, corpo: dict) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def do_OPTIONS(self):
        origem = self.headers.get("Origin")
        self.send_response(204 if origem_permitida(origem) else 403)
        if origem_permitida(origem):
            self.send_header("Access-Control-Allow-Origin", origem)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def _bloqueado(self) -> bool:
        origem = self.headers.get("Origin")
        if origem and not origem_permitida(origem):
            self._responder(403, {"erro": f"Origem não permitida: {origem}"})
            return True
        return False

    def _corpo(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 10_000:
            raise ErroCartao("Pedido grande demais.")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self._bloqueado():
            return
        if self.path.split("?")[0] != "/status":
            return self._responder(404, {"erro": "Rota desconhecida."})
        estado = {"agente": VERSAO, "leitor": None, "cartao": False, "uid": None}
        with _trava:
            try:
                leitor, conexao, nome = abrir_leitor()
                estado.update(leitor=nome, cartao=True, uid=leitor.uid())
                conexao.disconnect()
            except ErroCartao as e:
                msg = str(e)
                if msg.startswith("SEM_CARTAO:"):
                    estado["leitor"] = msg.split(":", 1)[1]
                elif msg != "SEM_LEITOR":
                    estado["erro"] = msg
            except Exception as e:                       # driver/serviço de cartão parado
                estado["erro"] = f"{type(e).__name__}: {e}"
        self._responder(200, estado)

    def do_POST(self):
        if self._bloqueado():
            return
        rota = self.path.split("?")[0]
        if rota not in ("/ler", "/gravar"):
            return self._responder(404, {"erro": "Rota desconhecida."})
        try:
            corpo = self._corpo()
            with _trava:
                leitor, conexao, _ = abrir_leitor()
                try:
                    if rota == "/ler":
                        resultado = {"uid": leitor.uid(), "blocos": leitor.ler(corpo.get("blocos") or [1, 2, 4, 5])}
                    else:
                        blocos = corpo.get("blocos")
                        if not isinstance(blocos, dict) or not blocos:
                            raise ErroCartao("Informe os blocos a gravar.")
                        resultado = leitor.gravar(blocos)
                finally:
                    conexao.disconnect()
            self._responder(200, resultado)
        except ErroCartao as e:
            msg = str(e)
            if msg == "SEM_LEITOR":
                msg = "Leitor RFID não encontrado. Confira o cabo USB e o driver do ACR122U."
            elif msg.startswith("SEM_CARTAO:"):
                msg = "Nenhum cartão no leitor. Aproxime o cartão e tente de novo."
            self._responder(409, {"erro": msg})
        except Exception as e:
            log.exception("Falha inesperada")
            self._responder(500, {"erro": f"{type(e).__name__}: {e}"})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    except OSError:
        print(f"O agente RFID já está aberto (porta {PORTA} em uso).")
        input("Pressione Enter para fechar.")
        sys.exit(1)
    print("=" * 60)
    print(f"  Agente RFID JKControl {VERSAO}")
    print(f"  Pronto em http://127.0.0.1:{PORTA}")
    print("  Deixe esta janela aberta enquanto grava cartões no app.")
    print("=" * 60)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
