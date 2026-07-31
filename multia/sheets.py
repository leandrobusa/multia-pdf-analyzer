"""
sheets.py — Integração com Google Sheets e gerenciamento de pastas.

Registra laudos na planilha de KPIs ao analisar um PDF e
cria a estrutura de pastas para cada matrícula.

Dependências:
    pip install google-auth requests
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Callable, Optional
from urllib.parse import quote as url_quote

import requests


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1W4J7d1WWClUKFYtPI395_VzgqS57qni65Ba16VftSaU"
ABA_PADRAO     = "LEANDRO"
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _obter_token(credentials_path: str) -> str:
    """Obtém um access token OAuth2 via Service Account.

    Args:
        credentials_path: Caminho para o arquivo credentials.json.

    Returns:
        Access token como string.

    Raises:
        FileNotFoundError: Se credentials_path não existir.
        Exception: Se a autenticação falhar.
    """
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    with open(credentials_path, "rb") as fh:
        raw = fh.read()

    # Remove espaços não-quebráveis que corrompem o JSON
    info = json.loads(raw.decode("utf-8").replace("\u00a0", " "))

    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


# ---------------------------------------------------------------------------
# Google Sheets API (baixo nível)
# ---------------------------------------------------------------------------

def _sheets_get(token: str, range_: str, aba: str = ABA_PADRAO) -> list:
    """Lê valores de um intervalo da planilha.

    Args:
        token: Access token OAuth2.
        range_: Intervalo no formato A1 (ex: "B:B").
        aba: Nome da aba a consultar.

    Returns:
        Lista de linhas com valores.
    """
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        f"/values/{url_quote(f'{aba}!{range_}', safe='!:')}"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("values", [])


def _sheets_escrever_linha(
    token: str,
    linha: int,
    valores: list,
    aba: str = ABA_PADRAO,
) -> None:
    """Escreve valores em uma linha específica da planilha.

    Args:
        token: Access token OAuth2.
        linha: Número da linha (1-indexado).
        valores: Lista de valores a escrever nas colunas A-F.
        aba: Nome da aba de destino.
    """
    intervalo = f"{aba}!A{linha}:F{linha}"
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        f"/values/{url_quote(intervalo, safe='!:')}?valueInputOption=USER_ENTERED"
    )
    resp = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"values": [valores]},
        timeout=30,
    )
    resp.raise_for_status()


def _proxima_linha_vazia(token: str, aba: str = ABA_PADRAO) -> int:
    """Retorna o número da próxima linha vazia na coluna B.

    Args:
        token: Access token OAuth2.
        aba: Nome da aba a consultar.

    Returns:
        Índice da próxima linha disponível (1-indexado).
    """
    valores = _sheets_get(token, "B:B", aba=aba)
    return len(valores) + 1


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------

def registrar_laudo(
    credentials_path: str,
    codigo_infoel: str,
    matricula: str,
    tipo_imovel: str,
    log_fn: Optional[Callable[[str], None]] = None,
    aba_nome: str = ABA_PADRAO,
) -> bool:
    """Registra um laudo na planilha de KPIs com status "Em andamento".

    Insere uma nova linha com: data, código Infoel, matrícula,
    status inicial e tipo do imóvel.

    Args:
        credentials_path: Caminho para o credentials.json do Service Account.
        codigo_infoel: Código do laudo na plataforma Infoel.
        matricula: Número da matrícula do imóvel.
        tipo_imovel: "RURAL" ou "URBANO".
        log_fn: Função opcional para registrar mensagens de progresso.
        aba_nome: Nome da aba da planilha a atualizar.

    Returns:
        True se o registro foi bem-sucedido, False em caso de erro.
    """
    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    try:
        log("[sheets] Autenticando no Google Sheets...")
        token = _obter_token(credentials_path)

        data_hoje = datetime.datetime.now().strftime("%d/%m/%Y")
        tipo      = "Rural" if str(tipo_imovel).upper() == "RURAL" else "Urbano"

        # Colunas: Data | Código Infoel | Matrícula | Observação | Data Entrega | Tipo
        valores = [data_hoje, str(codigo_infoel), str(matricula), "Em andamento", "", tipo]

        proxima = _proxima_linha_vazia(token, aba=aba_nome)
        log(f"[sheets] Inserindo na linha {proxima} da aba '{aba_nome}'...")
        _sheets_escrever_linha(token, proxima, valores, aba=aba_nome)
        log(f"[sheets] Registrado: {data_hoje} | {codigo_infoel} | {matricula} | {tipo}")
        return True

    except Exception as exc:
        log(f"[sheets] Erro ao registrar laudo: {exc}")
        return False


def criar_pasta_matricula(
    pasta_base: str,
    matricula: str,
    log_fn: Optional[Callable[[str], None]] = None,
) -> str:
    """Cria a pasta de trabalho para uma matrícula dentro da pasta base.

    O nome da pasta é sanitizado para remover caracteres inválidos no Windows.

    Args:
        pasta_base: Diretório raiz onde a pasta será criada.
        matricula: Número da matrícula (usado como nome da pasta).
        log_fn: Função opcional para registrar mensagens de progresso.

    Returns:
        Caminho absoluto da pasta criada (ou já existente).
    """
    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    nome_pasta = str(matricula)
    # Campos como "DOCUMENTO" do Infoel podem trazer mais de uma matrícula
    # separadas por quebra de linha (ex: avaliação que cobre 2 matrículas) —
    # vira " - " em vez de quebrar o caminho no Windows.
    nome_pasta = re.sub(r"[\r\n]+", " - ", nome_pasta)
    nome_pasta = nome_pasta.replace("/", "-").replace("\\", "-")
    nome_pasta = re.sub(r'[<>:"|?*]', "", nome_pasta)
    nome_pasta = re.sub(r"\s+", " ", nome_pasta).strip()
    caminho    = os.path.join(pasta_base, nome_pasta)

    if not os.path.exists(caminho):
        os.makedirs(caminho, exist_ok=True)
        log(f"[pasta] Criada: {caminho}")
    else:
        log(f"[pasta] Já existe: {caminho}")

    return caminho
