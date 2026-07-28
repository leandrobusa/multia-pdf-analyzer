"""
merge_multiplo.py — Mescla os dados de várias matrículas (extraídas com
PROMPT_MULTIPLO) num único pacote de campos para o laudo do MPA principal,
no mesmo formato que a análise de 1 PDF já produz — assim capa, vistoria e
fotos continuam funcionando sem precisar de lógica nova.

Regras:
- cri: valor que se repetir entre as matrículas ("universal"); em caso de
  empate entre 2 matrículas com CRI diferente, usa o da maior área.
- proprietarios / cidades_vizinhas: união, sem repetir.
- demais campos simples: comum se igual em todas, senão junta na mesma
  linha "Matrícula X: valor | Matrícula Y: valor".
- averbacoes / averbacoes_classificadas / analise_juridica: concatenados de
  todas as matrículas, cada item identificado por "[Mat. X]".
- vaga_garagem: true se qualquer matrícula tiver.
- area_total: NUNCA entra no dict mesclado (fica "-" na capa, por decisão
  do usuário) — usar calcular_areas() para mostrar no log (separada e soma).
- coordenadas: NÃO são mescladas aqui — cada matrícula mantém as suas
  próprias, usadas depois pelo gerador de KML.
- matricula_cancelada: não entra no dict — usar matriculas_canceladas()
  para alertar separadamente no log.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .utils import normalizar_area

SEPARADOR = " | "

_CAMPOS_SIMPLES = [
    "testada", "profundidade", "incra", "car", "rodovias_acesso",
    "numero", "bairro", "cidade", "uf",
    "tipo_imovel", "unidade", "pavimento",
]
_CAMPOS_JUNTAR_E = ["endereco", "complemento"]
_CAMPOS_LISTA = ["proprietarios", "cidades_vizinhas"]
_CATEGORIAS_AVERBACOES = [
    "construcoes_demoliciones", "areas_nao_edificaveis", "ferrovias_gasodutos",
    "desmembramentos", "remembramentos", "reservas_legais",
]


def _numero(m: dict) -> str:
    return str(m.get("numero_matricula") or "?").strip()


def _pares_com_valor(matriculas: list[dict], campo: str) -> list[tuple[str, Any]]:
    out = []
    for m in matriculas:
        v = m.get(campo)
        if v not in (None, "", "null"):
            out.append((_numero(m), v))
    return out


def _mesclar_valor_simples(matriculas: list[dict], campo: str) -> Any:
    pares = _pares_com_valor(matriculas, campo)
    if not pares:
        return None
    distintos = {str(v) for _, v in pares}
    if len(distintos) == 1:
        return pares[0][1]
    return SEPARADOR.join(f"Matrícula {num}: {v}" for num, v in pares)


def _mesclar_juntando_e(matriculas: list[dict], campo: str) -> Any:
    """Para campos como endereço/complemento: junta os valores distintos com
    'e', sem rótulo de matrícula (ex: 'Lote 01 e Lote 05') — diferente dos
    campos simples, que mostram 'Matrícula X: valor | Matrícula Y: valor'."""
    pares = _pares_com_valor(matriculas, campo)
    if not pares:
        return None
    distintos = []
    vistos = set()
    for _, v in pares:
        chave = str(v).strip().lower()
        if chave not in vistos:
            vistos.add(chave)
            distintos.append(str(v).strip())
    if len(distintos) == 1:
        return distintos[0]
    if len(distintos) == 2:
        return f"{distintos[0]} e {distintos[1]}"
    return ", ".join(distintos[:-1]) + " e " + distintos[-1]


def _mesclar_lista(matriculas: list[dict], campo: str) -> list:
    vistos = set()
    resultado = []
    for m in matriculas:
        for item in (m.get(campo) or []):
            chave = str(item).strip().lower()
            if chave and chave not in vistos:
                vistos.add(chave)
                resultado.append(str(item).strip())
    return resultado


def _concatenar_lista(matriculas: list[dict], campo: str) -> list:
    resultado = []
    for m in matriculas:
        resultado.extend(m.get(campo) or [])
    return resultado


def _area_numerica(valor: str) -> float:
    """Converte '1,0512 ha' / '600,00 m²' num float comparável (só a magnitude)."""
    if not valor:
        return 0.0
    n = normalizar_area(valor)
    try:
        return float(str(n).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _mesclar_cri(matriculas: list[dict]) -> Any:
    pares = _pares_com_valor(matriculas, "cri")
    if not pares:
        return None
    contagem = Counter(v for _, v in pares)
    mais_comum, qtd = contagem.most_common(1)[0]
    if qtd > 1:
        return mais_comum
    if len(pares) == 2:
        candidatos = [(m.get("cri"), _area_numerica(m.get("area_total"))) for m in matriculas if m.get("cri")]
        candidatos.sort(key=lambda c: c[1], reverse=True)
        return candidatos[0][0]
    # 3+ matrículas, todas com CRI diferente entre si — sem maioria clara
    return SEPARADOR.join(f"Matrícula {num}: {v}" for num, v in pares)


def _mesclar_averbacoes(matriculas: list[dict]):
    linhas = []
    for m in matriculas:
        av = m.get("averbacoes")
        if not av or av == "Não há averbações de construção ou demolição":
            continue
        if isinstance(av, list):
            for item in av:
                linhas.append(f"[Mat. {_numero(m)}] {item}")
        else:
            linhas.append(f"[Mat. {_numero(m)}] {av}")
    return linhas if linhas else "Não há averbações de construção ou demolição"


def _mesclar_averbacoes_classificadas(matriculas: list[dict]) -> dict:
    resultado = {cat: [] for cat in _CATEGORIAS_AVERBACOES}
    for m in matriculas:
        classificadas = m.get("averbacoes_classificadas") or {}
        if not isinstance(classificadas, dict):
            continue
        for cat in _CATEGORIAS_AVERBACOES:
            for item in (classificadas.get(cat) or []):
                resultado[cat].append(f"[Mat. {_numero(m)}] {item}")
    return resultado


def _mesclar_analise_juridica(matriculas: list[dict]) -> list:
    itens = []
    for m in matriculas:
        for item in (m.get("analise_juridica") or []):
            if not isinstance(item, dict):
                continue
            novo = dict(item)
            novo["descricao"] = f"[Mat. {_numero(m)}] {item.get('descricao', '')}"
            itens.append(novo)
    return itens


def calcular_areas(matriculas: list[dict]) -> dict:
    """Área de cada matrícula + soma total — só para exibir no log e servir
    de base para a escolha 'somar'/'dividir' no parecer. Nunca é escrita na
    capa por esse fluxo."""
    itens = []
    soma = 0.0
    for m in matriculas:
        area_raw = m.get("area_total")
        itens.append({
            "numero": _numero(m),
            "area_raw": area_raw or "(não encontrado)",
            "area_num": _area_numerica(area_raw),
        })
        soma += _area_numerica(area_raw)
    unidade = "ha" if any((m.get("tipo_imovel") or "").upper() == "RURAL" for m in matriculas) else "m²"
    return {"itens": itens, "soma": soma, "unidade": unidade}


def matriculas_canceladas(matriculas: list[dict]) -> list[dict]:
    """Matrículas sinalizadas pelo Gemini como canceladas/encerradas."""
    out = []
    for m in matriculas:
        mc = m.get("matricula_cancelada") or {}
        if mc.get("cancelada"):
            out.append({"numero": _numero(m), "motivo": mc.get("motivo")})
    return out


def mesclar_matriculas(matriculas: list[dict]) -> dict:
    """Monta o dict mesclado no mesmo formato que o PROMPT_NORMAL produziria
    para 1 PDF, pronto para reaproveitar toda a lógica existente do MPA
    (preencher_capa, vistoria, fotos etc.)."""
    mesclado: dict[str, Any] = {}
    for campo in _CAMPOS_SIMPLES:
        mesclado[campo] = _mesclar_valor_simples(matriculas, campo)
    for campo in _CAMPOS_JUNTAR_E:
        mesclado[campo] = _mesclar_juntando_e(matriculas, campo)
    for campo in _CAMPOS_LISTA:
        mesclado[campo] = _mesclar_lista(matriculas, campo)
    mesclado["cri"] = _mesclar_cri(matriculas)
    mesclado["averbacoes"] = _mesclar_averbacoes(matriculas)
    mesclado["averbacoes_classificadas"] = _mesclar_averbacoes_classificadas(matriculas)
    mesclado["analise_juridica"] = _mesclar_analise_juridica(matriculas)
    mesclado["vaga_garagem"] = any(m.get("vaga_garagem") is True for m in matriculas)
    mesclado["areas_privativas"] = _concatenar_lista(matriculas, "areas_privativas") or None
    return mesclado
