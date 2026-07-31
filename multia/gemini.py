"""
gemini.py — Pipeline de comunicação com a API Google Gemini.

Fluxo principal:
    1. Upload do PDF via File API (binário raw — evita SSLEOFError)
    2. Aguarda o arquivo atingir state=ACTIVE
    3. Gera conteúdo com fallback automático de modelos
    4. Remove o arquivo da File API após uso

Modelos tentados em ordem: gemini-2.5-flash → gemini-2.0-flash
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests
from requests.exceptions import ConnectionError as ReqConnError
from requests.exceptions import HTTPError, SSLError, Timeout

from .config import GEMINI_KEY


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_MODELOS        = ("gemini-2.5-flash", "gemini-2.0-flash")
_ERROS_RETRY    = frozenset({429, 500, 502, 503, 504})
_MAX_TENTATIVAS = 5
_BASE_URL       = "https://generativelanguage.googleapis.com"

# Limite máximo de tokens de saída por modelo — evita resposta cortada no meio
# (JSON incompleto) em documentos grandes, com muitas averbações/coordenadas.
_MAX_OUTPUT_TOKENS = {
    "gemini-2.5-flash": 65536,
    "gemini-2.0-flash": 8192,
}


def _generation_config(modelo: str) -> dict:
    """Monta o generationConfig apropriado para o modelo.

    O gemini-2.5-flash é um modelo "thinking" — por padrão reserva parte do
    limite de tokens para raciocínio interno antes de responder, o que em
    documentos grandes pode consumir boa parte do limite e cortar o JSON no
    meio. Como a tarefa aqui é extração fiel de dados (não raciocínio
    complexo), desligamos o "pensamento" para liberar o limite inteiro
    para a resposta em si.
    """
    config = {
        "responseMimeType": "application/json",
        "maxOutputTokens": _MAX_OUTPUT_TOKENS.get(modelo, 8192),
    }
    if modelo.startswith("gemini-2.5"):
        config["thinkingConfig"] = {"thinkingBudget": 0}
    return config


# ---------------------------------------------------------------------------
# Utilitários internos
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Print seguro para Windows — evita UnicodeEncodeError com emojis."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def _com_retry(fn, *args, **kwargs) -> Any:
    """Executa fn com backoff exponencial para erros de rede e HTTP 5xx/429.

    Args:
        fn: Callable a executar.
        *args, **kwargs: Argumentos repassados para fn.

    Returns:
        Valor retornado por fn em caso de sucesso.

    Raises:
        RuntimeError: Se todas as tentativas falharem.
    """
    ultimo_erro = None
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            return fn(*args, **kwargs)
        except (SSLError, ReqConnError, Timeout) as exc:
            espera = 2 ** tentativa
            _log(f"   Tentativa {tentativa}/{_MAX_TENTATIVAS} — rede/SSL, aguardando {espera}s ({exc})")
            time.sleep(espera)
            ultimo_erro = exc
        except HTTPError as exc:
            codigo = exc.response.status_code if exc.response is not None else 0
            if codigo in _ERROS_RETRY:
                espera = 2 ** tentativa
                _log(f"   Tentativa {tentativa}/{_MAX_TENTATIVAS} — HTTP {codigo}, aguardando {espera}s")
                time.sleep(espera)
                ultimo_erro = exc
            else:
                raise
    raise RuntimeError(f"Operação falhou após {_MAX_TENTATIVAS} tentativas. Último erro: {ultimo_erro}")


def _checar_truncamento(resposta_bruta: dict) -> None:
    """Levanta erro claro se a resposta foi cortada por estourar o limite de tokens
    de saída, em vez de deixar o parsing de JSON falhar com erro genérico.
    """
    candidatos = resposta_bruta.get("candidates") or []
    if candidatos and candidatos[0].get("finishReason") == "MAX_TOKENS":
        raise RuntimeError(
            "Resposta cortada por estourar o limite de tokens de saída — "
            "documento provavelmente grande demais (muitas averbações/coordenadas)."
        )


def _extrair_json(texto: str) -> dict:
    """Extrai e parseia o objeto JSON da resposta do modelo.

    Args:
        texto: Texto bruto retornado pelo Gemini.

    Returns:
        Dicionário parseado.

    Raises:
        json.JSONDecodeError: Se o JSON for inválido.
        ValueError: Se nenhum objeto JSON for encontrado.
    """
    limpo = re.sub(r"```json\s*", "", texto)
    limpo = re.sub(r"```\s*", "", limpo).strip()
    inicio = limpo.find("{")
    fim    = limpo.rfind("}") + 1
    if inicio == -1:
        raise ValueError("Nenhum objeto JSON encontrado na resposta.")
    return json.loads(limpo[inicio:fim])


# ---------------------------------------------------------------------------
# File API
# ---------------------------------------------------------------------------

def _upload_pdf(pdf_bytes: bytes) -> str:
    """Faz upload do PDF pela File API usando protocolo raw.

    O protocolo raw evita o SSLEOFError causado por payloads JSON muito grandes.

    Args:
        pdf_bytes: Conteúdo binário do PDF.

    Returns:
        URI do arquivo no Gemini (ex: "files/abc123").
    """
    url = f"{_BASE_URL}/upload/v1beta/files?key={GEMINI_KEY}"

    def _fazer_upload():
        resp = requests.post(
            url,
            headers={"X-Goog-Upload-Protocol": "raw", "Content-Type": "application/pdf"},
            data=pdf_bytes,
            timeout=(10, 120),
        )
        resp.raise_for_status()
        return resp.json()["file"]["uri"]

    _log(f"   Upload do PDF ({len(pdf_bytes) // 1024} KB)...")
    uri = _com_retry(_fazer_upload)
    _log(f"   Upload concluído: {uri}")
    return uri


def _aguardar_ativo(file_uri: str, timeout_s: int = 60) -> None:
    """Aguarda o arquivo atingir state=ACTIVE no Gemini.

    Args:
        file_uri: URI retornada pelo upload.
        timeout_s: Tempo máximo de espera em segundos.

    Raises:
        RuntimeError: Se o arquivo falhar ou o timeout for atingido.
    """
    file_id = file_uri.split("/files/")[-1]
    url     = f"{_BASE_URL}/v1beta/files/{file_id}?key={GEMINI_KEY}"

    for _ in range(timeout_s // 3):
        resp  = requests.get(url, timeout=(10, 30))
        resp.raise_for_status()
        state = resp.json().get("state", "")
        if state == "ACTIVE":
            return
        if state == "FAILED":
            raise RuntimeError("Arquivo rejeitado pelo Gemini (state=FAILED).")
        time.sleep(3)

    raise RuntimeError(f"Timeout aguardando arquivo ACTIVE após {timeout_s}s.")


def _deletar(file_uri: str) -> None:
    """Remove o arquivo da File API (limpeza silenciosa, erros ignorados)."""
    try:
        file_id = file_uri.split("/files/")[-1]
        requests.delete(
            f"{_BASE_URL}/v1beta/files/{file_id}?key={GEMINI_KEY}",
            timeout=(10, 30),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Geração de conteúdo
# ---------------------------------------------------------------------------

def _gerar(file_uri: str, prompt: str, modelo: str) -> dict:
    """Chama generateContent referenciando o file_uri.

    Args:
        file_uri: URI do arquivo já em estado ACTIVE.
        prompt: Texto do prompt a enviar junto com o arquivo.
        modelo: Nome do modelo Gemini a usar.

    Returns:
        Resposta bruta da API como dicionário.
    """
    url = f"{_BASE_URL}/v1beta/models/{modelo}:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"file_data": {"mime_type": "application/pdf", "file_uri": file_uri}},
                {"text": prompt},
            ]
        }],
        "generationConfig": _generation_config(modelo),
    }

    def _fazer_geracao():
        resp = requests.post(url, json=payload, timeout=(10, 300))
        resp.raise_for_status()
        return resp.json()

    return _com_retry(_fazer_geracao)


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------

def chamar_gemini(pdf_bytes: bytes, prompt: str) -> dict:
    """Analisa um PDF com o Gemini e retorna os dados estruturados como dict.

    Fluxo: upload → aguarda ACTIVE → gera com fallback → limpa → parseia JSON.

    Args:
        pdf_bytes: Conteúdo binário do PDF.
        prompt: Instrução de análise enviada ao modelo.

    Returns:
        Dicionário com os dados extraídos do PDF.

    Raises:
        RuntimeError: Se todos os modelos falharem.
        json.JSONDecodeError: Se a resposta não for JSON válido.
    """
    file_uri   = _upload_pdf(pdf_bytes)
    _aguardar_ativo(file_uri)

    dados       = None
    ultimo_erro = None

    for modelo in _MODELOS:
        try:
            _log(f"   Gerando com {modelo}...")
            resposta_bruta = _gerar(file_uri, prompt, modelo)
            _checar_truncamento(resposta_bruta)
            texto = resposta_bruta["candidates"][0]["content"]["parts"][0]["text"]
            dados = _extrair_json(texto)
            _log(f"   Resposta recebida de {modelo}")
            break
        except Exception as exc:
            _log(f"   {modelo} falhou: {exc} — tentando próximo modelo...")
            ultimo_erro = exc

    _deletar(file_uri)

    if dados is None:
        raise RuntimeError(f"Todos os modelos falharam. Último erro: {ultimo_erro}")

    return dados


def chamar_gemini_multiplo(lista_pdf_bytes: list[bytes], prompt: str) -> dict:
    """Analisa vários PDFs numa única chamada ao Gemini (todos os arquivos
    enviados juntos, na mesma requisição) e retorna os dados estruturados.

    Usado para mesclar várias matrículas — o Gemini recebe todos os PDFs de
    uma vez e retorna um JSON com a análise de cada um, na mesma ordem em
    que os arquivos foram enviados.

    Args:
        lista_pdf_bytes: lista com o conteúdo binário de cada PDF, na ordem
            em que devem ser analisados.
        prompt: instrução de análise enviada ao modelo.

    Returns:
        Dicionário com os dados extraídos.

    Raises:
        RuntimeError: Se todos os modelos falharem.
        json.JSONDecodeError: Se a resposta não for JSON válido.
    """
    file_uris = []
    try:
        for i, pdf_bytes in enumerate(lista_pdf_bytes):
            _log(f"   Upload do PDF {i+1}/{len(lista_pdf_bytes)}...")
            uri = _upload_pdf(pdf_bytes)
            _aguardar_ativo(uri)
            file_uris.append(uri)

        parts = [{"file_data": {"mime_type": "application/pdf", "file_uri": uri}} for uri in file_uris]
        parts.append({"text": prompt})

        def _fazer_geracao(modelo):
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": _generation_config(modelo),
            }
            resp = requests.post(
                f"{_BASE_URL}/v1beta/models/{modelo}:generateContent?key={GEMINI_KEY}",
                json=payload, timeout=(10, 300),
            )
            resp.raise_for_status()
            return resp.json()

        dados       = None
        ultimo_erro = None
        for modelo in _MODELOS:
            try:
                _log(f"   Gerando com {modelo}...")
                resposta_bruta = _com_retry(_fazer_geracao, modelo)
                _checar_truncamento(resposta_bruta)
                texto = resposta_bruta["candidates"][0]["content"]["parts"][0]["text"]
                dados = _extrair_json(texto)
                _log(f"   Resposta recebida de {modelo}")
                break
            except Exception as exc:
                _log(f"   {modelo} falhou: {exc} — tentando próximo modelo...")
                ultimo_erro = exc

        if dados is None:
            raise RuntimeError(f"Todos os modelos falharam. Último erro: {ultimo_erro}")

        return dados
    finally:
        for uri in file_uris:
            _deletar(uri)


def chamar_gemini_azimutes(pdf_bytes: bytes) -> dict:
    """Segunda chamada ao Gemini focada exclusivamente em azimutes e UTM.

    Usada como complemento quando a análise principal retorna menos de
    3 pontos UTM — extrai azimutes e distâncias para cálculo do polígono.

    Args:
        pdf_bytes: Conteúdo binário do mesmo PDF já analisado.

    Returns:
        Dicionário com ponto_inicial e lista de azimutes, ou {} se não encontrado.
    """
    from .prompts import PROMPT_AZIMUTES

    _log("   [azimutes] Upload do PDF para análise de azimutes...")
    file_uri = _upload_pdf(pdf_bytes)
    _aguardar_ativo(file_uri)

    resposta_bruta = None
    ultimo_erro    = None

    for modelo in _MODELOS:
        try:
            _log(f"   [azimutes] Gerando com {modelo}...")
            resposta_bruta = _gerar(file_uri, PROMPT_AZIMUTES, modelo)
            _log("   [azimutes] Resposta recebida")
            break
        except Exception as exc:
            _log(f"   [azimutes] {modelo} falhou: {exc}")
            ultimo_erro = exc

    _deletar(file_uri)

    if resposta_bruta is None:
        _log(f"   [azimutes] Todos os modelos falharam: {ultimo_erro}")
        return {}

    try:
        _checar_truncamento(resposta_bruta)
        texto = resposta_bruta["candidates"][0]["content"]["parts"][0]["text"]
        return _extrair_json(texto)
    except Exception as exc:
        _log(f"   [azimutes] Erro ao parsear resposta: {exc}")
        return {}
