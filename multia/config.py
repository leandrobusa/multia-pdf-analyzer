"""
config.py — Configurações e credenciais do sistema.

Carrega variáveis do arquivo .env localizado na mesma pasta
do executável (compatível com PyInstaller --onefile).
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def _resolver_diretorio_base() -> str:
    """Retorna o diretório base correto tanto para script quanto para .exe.

    Com PyInstaller --onefile, sys.executable aponta para o .exe real,
    enquanto sys._MEIPASS aponta para a pasta temporária de extração.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _carregar_env() -> bool:
    """Carrega variáveis do arquivo .env para os.environ.

    Returns:
        True se o arquivo foi encontrado e carregado, False caso contrário.
    """
    base = _resolver_diretorio_base()
    candidates = [
        os.path.join(base, ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    for env_path in candidates:
        if not os.path.exists(env_path):
            continue
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
        return True
    return False


# Carrega .env na importação do módulo
ENV_CARREGADO: bool = _carregar_env()

# Credenciais — lidas do ambiente após carregar o .env
GEMINI_KEY:      str = os.environ.get("GEMINI_API_KEY", "")
AUTH_BASIC:      str = os.environ.get("AUTH_BASIC", "")
JWT_PADRAO:      str = os.environ.get("JWT_PADRAO", "")
AUTH_BASIC_MAIS: str = os.environ.get("AUTH_BASIC_MAIS", "")
JWT_PADRAO_MAIS: str = os.environ.get("JWT_PADRAO_MAIS", "")
API_BASE:        str = os.environ.get("API_BASE", "https://api.infoel.info")

# Mantém compatibilidade com código que usa _ENV_CARREGADO
_ENV_CARREGADO = ENV_CARREGADO


# ---------------------------------------------------------------------------
# Dados estáticos do formulário de parecer
# ---------------------------------------------------------------------------

OPCOES = [
    {"id":"posicao",      "label":"Posição",               "tipo":"radio",
     "opcoes":["Meio de Quadra","Esquina de Quadra","Subesquina","Curso de Estrada Rural","Área Encravada"],
     "opcoes_parecer":["no meio de quadra","na esquina de quadra","na subesquina","no curso de estrada rural","area encravada"]},
    {"id":"relevo",       "label":"Relevo",                "tipo":"relevo_custom"},
    {"id":"vegetacao",    "label":"Cobertura Vegetal",      "tipo":"radio",
     "opcoes":["Limpo","Com Vegetação Baixa","Com Vegetação Média","Com Vegetação Alta"],
     "opcoes_parecer":["limpo","com vegetação baixa","com vegetação média","com vegetação alta"]},
    {"id":"veg_extra",    "label":"Vegetação Especial",     "tipo":"checkbox",
     "opcoes":["Com Área de Reserva Legal","Com Área de Preservação Permanente","Com Área de Mata Nativa"],
     "opcoes_parecer":["com área de reserva legal","com área de preservação permanente","com área de mata nativa"]},
    {"id":"formato",      "label":"Formato",                "tipo":"radio",
     "opcoes":["Retangular","Triangular","Quadrado","Polígono Irregular"],
     "opcoes_parecer":["retangular","triangular","quadrado","polígono irregular"]},
    {"id":"solo",         "label":"Superfície do Solo",     "tipo":"radio",
     "opcoes":["Seco","Úmido"],
     "opcoes_parecer":["seco","úmido"]},
    {"id":"cercamento",   "label":"Cercamento",             "tipo":"radio",
     "opcoes":["Murado na Frente, nas Laterais e nos Fundos","Cercado na Frente, nas Laterais e nos Fundos",
               "Murado na Frente e Cercado nos Fundos e Laterais","Sem Cercamento"],
     "opcoes_parecer":["murado na frente, nas laterais e nos fundos","cercado na frente, nas laterais e nos fundos",
               "murado na frente e cercado nos fundos e laterais","sem cercamento"]},
    {"id":"calcada",      "label":"Calçada",                "tipo":"radio",
     "opcoes":["Com Calçada em Blocos de Concreto","Com Calçada em Paver","Com Calçada em Pedras",
               "Com Calçada em Concreto","Com Calçada em Grama","Sem Calçada"],
     "opcoes_parecer":["com calçada em blocos de concreto","com calçada em paver","com calçada em pedras",
               "com calçada em concreto","com calçada em grama","sem calçada"]},
    {"id":"meio_fio",     "label":"Meio-Fio",               "tipo":"radio",
     "opcoes":["Meio-Fio em Concreto","Sem Meio-Fio"],
     "opcoes_parecer":["meio-fio em concreto","sem meio-fio"]},
    {"id":"extras",       "label":"Extras",                 "tipo":"checkbox",
     "opcoes":["Com Cerca Elétrica","Com Jardinagem Externa"],
     "opcoes_parecer":["com cerca elétrica","com jardinagem externa"]},
    {"id":"tipo_via",     "label":"Com acesso por uma",    "tipo":"radio",
     "opcoes":["Rua","Estrada","Rodovia (Às Margens)","Condomínio","Servidão de Passagem"],
     "opcoes_parecer":["rua","estrada","às margens de uma rodovia","condomínio","servidão de passagem"]},
    {"id":"pavimentacao", "label":"Pavimentação",           "tipo":"radio",
     "opcoes":["Sem Pavimentação","Com Pavimentação Asfáltica","Com Pavimentação em Calçamento"],
     "opcoes_parecer":["sem pavimentação","com pavimentação asfáltica","com pavimentação em calçamento"]},
    {"id":"conservacao",  "label":"Conservação da Via",     "tipo":"radio",
     "opcoes":["Razoável Estado de Conservação e Uso","Bom Estado de Conservação e Uso","Precário Estado de Conservação e Uso"],
     "opcoes_parecer":["razoável estado de conservação e uso","bom estado de conservação e uso","precário estado de conservação e uso"]},
    {"id":"tipo_rua",     "label":"Uso da Via",             "tipo":"radio",
     "opcoes":["Residencial","Predominantemente Residencial","Comercial","Predominantemente Comercial",
               "Comercial e Residencial","De Uso Industrial","Rural"],
     "opcoes_parecer":["residencial","predominantemente residencial","comercial","predominantemente comercial",
               "comercial e residencial","de uso industrial","rural"]},
    {"id":"padrao",       "label":"Padrão Construtivo",     "tipo":"radio",
     "opcoes":["Baixo","Médio/Baixo","Médio","Médio/Alto","Alto"],
     "opcoes_parecer":["baixo","médio/baixo","médio","médio/alto","alto"]},
    {"id":"dist_rodovia", "label":"Distância até a Rodovia","tipo":"texto","placeholder":"ex: 0,3 km"},
    {"id":"dist_centro",  "label":"Distância até o Centro", "tipo":"texto","placeholder":"ex: 1,0 km"},
]

RISCO_PERGUNTAS = [
    "Há indícios de contaminação no imóvel?",
    "Possui nascente no imóvel?",
    "Há indícios de atividade industrial no imóvel?",
    "Há indícios de atividade de mineração no imóvel?",
    "Há indícios de atividade de posto de gasolina ou tanque de combustíveis no imóvel?",
    "Há indícios de serviços de troca de óleo, galvanoplastia, lavanderia ou tinturaria no imóvel?",
    "Há indícios de aterro sanitário, lixão, ferro velho, cemitério ou entulhos no imóvel?",
    "Há indícios de resíduos líquidos ou sólidos no imóvel?",
    "Há armazenamento de baterias automotivas ou industriais usadas, derivados de petróleo, pesticidas, herbicidas, tintas, vernizes, ou outros componentes tóxicos no imóvel?",
    "Foi observada alguma atividade desativada no entorno nas mesmas características descritas acima?",
]

OBS_OPCIONAIS = [
    "Para maior precisão do croqui é necessário o serviço de georreferenciamento/topografia/agrimensura.",
    "Edificações não averbadas possuem dimensões aproximadas.",
    "Não foi possível realizar fotos internas do imóvel.",
    "Na matricula consta uma residência de madeira com 60,00m² que não existe mais no terreno, porém não consta averbação de demolição.",
    "Consta averbação de demolição referente à residência de madeira medindo 00,00m², conforme AV.",
    "Não foi possível identificar a divisão de cômodos das áreas averbadas e não averbadas em separado.",
    "Este laudo não leva em consideração vagas de garagem.",
]
