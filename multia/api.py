"""
api.py — Cliente da API REST da plataforma Infoel (MultiaAPI).

Encapsula todas as chamadas HTTP: busca de avaliações, salvamento de
campos, parecer, croqui, grupos de vistoria e anexos de arquivos.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

import requests

from .config import API_BASE, AUTH_BASIC, AUTH_BASIC_MAIS
from .utils import _normalizar_nome_foto, _title_grupo, _title_nome, normalizar_area


class MultiaAPI:
    """Cliente de acesso à API REST da plataforma Infoel.

    Args:
        jwt: Token JWT de autenticação do usuário.
        sistema: Sistema a acessar — "multia" (padrão) ou "multiamais".
    """

    def __init__(self, jwt: str, sistema: str = "multia") -> None:
        auth = AUTH_BASIC_MAIS if sistema == "multiamais" else AUTH_BASIC
        self.headers = {
            "authorization": auth,
            "jwt":           jwt,
            "Accept":        "*/*",
            "Origin":        f"https://{sistema}.infoel.info",
            "Referer":       f"https://{sistema}.infoel.info/",
        }

    def _url(self, path: str) -> str:
        return API_BASE + path

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def buscar_avaliacao(self, busca: str) -> list[dict]:
        """Busca avaliações pelo código REG ou texto livre.

        Args:
            busca: Código REG ou termo de busca.

        Returns:
            Lista de avaliações encontradas.
        """
        resp = requests.get(
            self._url("/multia/avaliacoes"),
            headers=self.headers,
            params={
                "page": 0, "pageSize": 50, "busca": busca,
                "sortField": "", "sortOrder": "", "REGSTATUS": "", "DATACRIACAO": "",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("dados", {}).get("avaliacoes", [])

    def buscar_vistoria(self, uuid: str) -> dict:
        """Retorna os dados de vistoria de uma avaliação.

        Args:
            uuid: Identificador único da avaliação.

        Returns:
            Dicionário com dados da vistoria.
        """
        resp = requests.get(
            self._url(f"/multia/buscardadosvistoriaimovel/{uuid}"),
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("dados", {})

    def buscar_dados_avaliacao(self, uuid: str) -> dict:
        """Retorna todos os dados cadastrais de uma avaliação.

        Args:
            uuid: Identificador único da avaliação.

        Returns:
            Dicionário completo com campos e dados da avaliação.
        """
        resp = requests.get(
            self._url(f"/multia/dadosavaliacao/{uuid}"),
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("dados", {})

    # ------------------------------------------------------------------
    # Salvamento de campos
    # ------------------------------------------------------------------

    def salvar_campo(self, uuid: str, campo_id: Any, valor: Any) -> dict:
        """Salva o valor de um campoTipoBem pelo seu ID.

        Args:
            uuid: Identificador da avaliação.
            campo_id: REG/ID do campo.
            valor: Valor a salvar.

        Returns:
            Resposta da API.
        """
        resp = requests.post(
            self._url(f"/multia/editaravaliacao/{uuid}"),
            headers=self.headers,
            data={f"campoTipoBem_{campo_id}": valor},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def salvar_campo_direto(self, uuid: str, chave: str, valor: Any) -> dict:
        """Salva um campo direto no endpoint editaravaliacao.

        Usado para campos como COMPLEMENTO, PARECERLAUDO, etc.

        Args:
            uuid: Identificador da avaliação.
            chave: Nome do campo na API.
            valor: Valor a salvar.

        Returns:
            Resposta da API.
        """
        resp = requests.post(
            self._url(f"/multia/editaravaliacao/{uuid}"),
            headers=self.headers,
            data={chave: valor},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def salvar_parecer(
        self,
        uuid: str,
        texto: str,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Salva o texto do parecer técnico.

        Tenta primeiro salvar via campoTipoBem identificado como "parecer",
        depois envia também via PARECERLAUDO direto para garantir compatibilidade.

        Args:
            uuid: Identificador da avaliação.
            texto: Texto completo do parecer.
            log_fn: Função opcional de log.

        Returns:
            Resposta da última chamada à API.
        """
        def log(msg: str) -> None:
            if log_fn:
                log_fn(msg)

        dados   = self.buscar_dados_avaliacao(uuid)
        campos  = dados.get("camposTipoBem", [])

        campo_parecer = next(
            (
                c for c in campos
                if "parecer" in (c.get("NOMECAMPO") or c.get("LABEL") or c.get("NOME") or "").lower()
            ),
            None,
        )

        if campo_parecer:
            cid = campo_parecer.get("REG") or campo_parecer.get("ID")
            nome = (campo_parecer.get("NOMECAMPO") or campo_parecer.get("LABEL") or "")
            log(f"   Campo parecer encontrado: '{nome}' (REG={cid})")
            resp = requests.post(
                self._url(f"/multia/editaravaliacao/{uuid}"),
                headers=self.headers,
                data={f"campoTipoBem_{cid}": texto},
                timeout=60,
            )
            log(f"   Status: {resp.status_code}")
            resp.raise_for_status()

        log("   Enviando via PARECERLAUDO...")
        resp = requests.post(
            self._url(f"/multia/editaravaliacao/{uuid}"),
            headers=self.headers,
            data={"PARECERLAUDO": texto},
            timeout=60,
        )
        log(f"   Status PARECERLAUDO: {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    def salvar_campos_capa(
        self,
        uuid: str,
        dados_pdf: dict,
        log_fn: Optional[Callable[[str], None]] = None,
        tipo_laudo: str = "normal",
    ) -> None:
        """Preenche todos os campos da capa da avaliação com dados do PDF.

        Salva campos diretos (endereço, bairro, cidade, UF, complemento) e
        campos dinâmicos mapeados (CRI, testada, INCRA, proprietários, etc.).

        Args:
            uuid: Identificador da avaliação.
            dados_pdf: Dicionário retornado pelo Gemini com dados extraídos.
            log_fn: Função opcional de log.
            tipo_laudo: "normal" ou "antigo".
        """
        def log(msg: str) -> None:
            if log_fn:
                log_fn(msg)

        dados  = self.buscar_dados_avaliacao(uuid)
        campos = dados.get("camposTipoBem", [])
        dados_atuais = {
            str(d.get("REGCAMPO") or d.get("REG", "")): d.get("VALOR", "")
            for d in dados.get("dadosTipoBem", [])
        }
        log(f"   {len(campos)} campos encontrados na avaliação")

        # Campos diretos
        CAMPOS_DIRETOS = {
            "COMPLEMENTO": "complemento",
            "ENDERECO":    "endereco",
            "NUMERO":      "numero",
            "BAIRRO":      "bairro",
            "CIDADE":      "cidade",
            "UF":          "uf",
        }
        CAMPOS_TITLE = {"ENDERECO", "BAIRRO", "CIDADE", "COMPLEMENTO"}

        _cidade = (dados_pdf.get("cidade") or "").strip()
        _uf     = (dados_pdf.get("uf") or "").strip()
        _rural  = (dados_pdf.get("tipo_imovel") or "").strip().upper() == "RURAL"

        payload_direto: dict = {}
        for chave_api, chave_pdf in CAMPOS_DIRETOS.items():
            val = dados_pdf.get(chave_pdf)
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            val = str(val).strip() if val else ""
            if not val or val in ("null", "None"):
                val = "-"
            if chave_api in CAMPOS_TITLE:
                val = _title_nome(val)
            payload_direto[chave_api] = val

        if _rural:
            # Imóvel rural: número e bairro sempre nesse formato, independente do que foi extraído
            payload_direto["NUMERO"] = "S/N"
            payload_direto["BAIRRO"] = "Interior"

        if payload_direto:
            for k, v in payload_direto.items():
                log(f"   [direto] {k} → '{v}'")
            resp = requests.post(
                self._url(f"/multia/editaravaliacao/{uuid}"),
                headers=self.headers,
                data=payload_direto,
                timeout=30,
            )
            resp.raise_for_status()

        # Campos dinâmicos via campoTipoBem
        IGNORAR = [
            "cep", "matricula", "fonte do croqui", "fonte croqui",
            "placa", "renavam", "chassi", "tração", "espécie", "categoria veicular",
            "modelo", "marca", "combustível", "emplacamento", "série", "fabricação",
            "horas em uso", "dimensões", "capacidade", "peso", "comprimento",
            "município emplacamento", "nº de série", "n° de série", "descri",
        ]
        MAPA = {
            "cri":               "cri",
            "testada":           "testada",
            "incra":             "incra",
            "inscr":             "incra",
            "imob":              "incra",
            "inscrição":         "incra",
            "inscricao":         "incra",
            "cidades vizinhas":  "cidades_vizinhas",
            "cidade vizinha":    "cidades_vizinhas",
            "rodovias de acesso":"rodovias_acesso",
            "rodovia de acesso": "rodovias_acesso",
            "rodovias":          "rodovias_acesso",
            "car":               "car",
            "proprietár":        "proprietarios",
            "proprietar":        "proprietarios",
            "proprietários":     "proprietarios",
            "proprietarios":     "proprietarios",
            "área total":        "area_total",
            "area total":        "area_total",
            "área (m":           "area_total",
            "area (m":           "area_total",
            "averbac":           "averbacoes",
            "coordenada":        "coordenadas_raw",
        }

        ignorar_coords = tipo_laudo == "normal"
        salvos = nao_mapeados = ignorados = 0

        for campo in campos:
            nome = (campo.get("NOMECAMPO") or campo.get("LABEL") or campo.get("NOME") or "").lower().strip()
            cid  = campo.get("REG") or campo.get("ID")
            if not cid:
                continue
            if any(ig in nome for ig in IGNORAR):
                ignorados += 1
                continue
            if ignorar_coords and "coordenada" in nome:
                ignorados += 1
                continue

            valor = None
            chave_matched = None
            for km, kp in MAPA.items():
                if km in nome:
                    valor         = dados_pdf.get(kp)
                    chave_matched = kp
                    break

            if chave_matched is None:
                nao_mapeados += 1
                continue
            if isinstance(valor, list):
                valor = ", ".join(str(v) for v in valor) if valor else ""
            else:
                valor = str(valor).strip() if valor is not None else ""
            if not valor or valor in ("null", "None"):
                valor = "-"

            if chave_matched == "area_total" and valor != "-":
                valor = normalizar_area(valor)

            elif chave_matched == "cri" and valor != "-":
                if _cidade and _cidade.lower() not in valor.lower():
                    sufixo = f" de {_title_nome(_cidade)}"
                    if _uf:
                        sufixo += f" - {_uf.upper()}"
                    valor += sufixo
                elif _uf and f"- {_uf.upper()}" not in valor.upper() and f"/{_uf.upper()}" not in valor.upper():
                    valor = f"{valor} - {_uf.upper()}"
                valor = _title_nome(valor)

            elif chave_matched == "proprietarios":
                valor = _title_nome(valor)

            val_atual = dados_atuais.get(str(cid), "")
            prefixo   = "   Sobrescrevendo" if val_atual and val_atual.strip() not in ("", "-", "null") else "   "
            log(f"{prefixo} '{nome}' → '{valor}'")
            self.salvar_campo(uuid, cid, valor)
            salvos += 1

        log(f"   {salvos} campo(s) preenchido(s), {ignorados} ignorado(s)")
        if nao_mapeados:
            log(f"   Não mapeados: {nao_mapeados} campo(s)")

    # ------------------------------------------------------------------
    # Croqui e imagens
    # ------------------------------------------------------------------

    def salvar_dados_croqui(
        self,
        uuid: str,
        coords: str,
        fonte: str = "Croqui baseado na descrição da matrícula do imóvel.",
    ) -> dict:
        resp = requests.post(
            self._url(f"/multia/salvardadoscroqui/{uuid}"),
            headers=self.headers,
            data={"fonteCroqui": fonte, "coordenadas": coords},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def salvar_imagem_croqui(self, uuid: str, img: bytes, filename: str = "croqui.kml") -> dict:
        hdrs = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        resp = requests.post(
            self._url(f"/multia/salvarimagemcroqui/{uuid}"),
            headers=hdrs,
            files={"IMAGEM": (filename, img, "application/vnd.google-earth.kml+xml")},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def anexar_arquivo(
        self,
        uuid: str,
        arquivo_bytes: bytes,
        filename: str,
        descricao: str,
        mostrar_laudo: str = "S",
    ) -> dict:
        """Anexa um arquivo PDF à avaliação.

        Args:
            uuid: Identificador da avaliação.
            arquivo_bytes: Conteúdo binário do arquivo.
            filename: Nome do arquivo (com extensão).
            descricao: Descrição exibida na plataforma.
            mostrar_laudo: "S" para exibir no laudo, "N" para ocultar.

        Returns:
            Resposta da API.
        """
        hdrs = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        resp = requests.post(
            self._url(f"/multia/adicionararquivo/{uuid}/BEM"),
            headers=hdrs,
            files={"arquivo": (filename, arquivo_bytes, "application/pdf")},
            data={"DESCRICAO": descricao, "observacao": "", "mostrarLaudo": mostrar_laudo},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def salvar_foto_croqui(self, uuid: str, imagem_bytes: bytes, filename: str) -> dict:
        """Envia uma imagem para a aba croqui da avaliação.

        Args:
            uuid: Identificador da avaliação.
            imagem_bytes: Conteúdo binário da imagem.
            filename: Nome do arquivo (usado para inferir o MIME type).

        Returns:
            Resposta da API.
        """
        hdrs = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        ext  = os.path.splitext(filename)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        resp = requests.post(
            self._url(f"/multia/salvarimagemcroqui/{uuid}"),
            headers=hdrs,
            files={"IMAGEM": (filename, imagem_bytes, mime)},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Grupos de vistoria
    # ------------------------------------------------------------------

    def renomear_anexo(self, reg: int, descricao: str, mostrar_laudo: str = "S") -> dict:
        """Renomeia um anexo existente via POST /multia/editaranexo/{reg}.

        Args:
            reg: REG do anexo a renomear.
            descricao: Nova descrição/nome do anexo.
            mostrar_laudo: "S" para exibir no laudo.

        Returns:
            Resposta da API.
        """
        resp = requests.post(
            self._url(f"/multia/editaranexo/{reg}"),
            headers=self.headers,
            data={"descricao": descricao, "observacao": "", "mostrarLaudo": mostrar_laudo},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def remover_imagem_croqui(self, uuid: str, reg_imagem: int) -> dict:
        """Remove uma imagem do croqui via GET /multia/removerimagemcroqui/{uuid}/{reg}.

        Args:
            uuid: Identificador da avaliação.
            reg_imagem: REG da imagem a remover.

        Returns:
            Resposta da API.
        """
        resp = requests.get(
            self._url(f"/multia/removerimagemcroqui/{uuid}/{reg_imagem}"),
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {}

    def adicionar_grupo(self, uuid: str, nome: str) -> dict:
        resp = requests.post(
            self._url(f"/multia/adicionargrupoimovel/{uuid}"),
            headers=self.headers,
            data={"NOME": nome},
            timeout=30,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {}

    def editar_grupo(
        self,
        uuid: str,
        reg: Any,
        nome: Optional[str] = None,
        construcao: str = "N",
        averbado: str = "N",
        area: str = "",
        unidade: str = "m²",
        valor_unidade: str = "",
        obs: str = "",
        nao_se_aplica: bool = False,
    ) -> dict:
        aplica    = "N" if nao_se_aplica else ("S" if area and str(area).strip() not in ("", "0", "0.00") else "N")
        area_api  = "0" if nao_se_aplica else (str(area).replace(",", ".") if area else "0")
        payload   = {
            "CONSTRUCAO":  construcao,
            "AVERBADO":    averbado,
            "AREA":        area_api,
            "APLICAAREA":  aplica,
            "TIPOMEDIDA":  unidade,
            "VALORUNIDADE":valor_unidade,
            "OBS":         obs,
        }
        if nome:
            payload["NOME"] = nome
        resp = requests.post(
            self._url(f"/multia/salvargrupoimovel/{uuid}/{reg}"),
            headers=self.headers,
            data=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def salvar_imagem_grupo(self, uuid: str, reg: Any, img: bytes, filename: str = "foto.jpg") -> dict:
        hdrs = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        resp = requests.post(
            self._url(f"/multia/salvarimagemgrupoimovel/{uuid}/{reg}"),
            headers=hdrs,
            files={"arquivo": (filename, img, "image/jpeg")},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def editar_descricao_imagem(self, uuid: str, reg_grupo: Any, reg_imagem: Any, descricao: str) -> dict:
        """Atualiza a descrição de uma imagem vinculada a um grupo.

        Args:
            uuid: Identificador da avaliação.
            reg_grupo: REG do grupo.
            reg_imagem: REG da imagem.
            descricao: Nova descrição.

        Returns:
            Resposta da API.
        """
        resp = requests.post(
            self._url(f"/multia/editarimagemgrupoimovel/{uuid}/{reg_grupo}/{reg_imagem}"),
            headers=self.headers,
            data={"DESCRICAO": descricao},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def validar_vistoria_nao_aplica(
        self,
        uuid: str,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> list[dict]:
        """Marca todos os grupos da vistoria como "Não se aplica" e normaliza descrições.

        Args:
            uuid: Identificador da avaliação.
            log_fn: Função opcional de log.

        Returns:
            Lista de grupos que já tinham dados preenchidos antes da operação.
        """
        def log(msg: str) -> None:
            if log_fn:
                log_fn(msg)

        dados  = self.buscar_vistoria(uuid)
        grupos = dados.get("grupos") or dados.get("gruposImoveis") or []

        if not grupos:
            log("   Tentando via dadosavaliacao...")
            dados2 = self.buscar_dados_avaliacao(uuid)
            grupos = dados2.get("gruposImoveis", [])

        # Índice de imagens por grupo
        imagens_por_grupo: dict[str, list] = {}
        for img in (dados.get("imagens") or []):
            rg = img.get("REGGRUPO") or img.get("regGrupo")
            ri = img.get("REG")      or img.get("reg")
            desc = str(img.get("DESCRICAO") or img.get("descricao") or "")
            if rg and ri:
                imagens_por_grupo.setdefault(str(rg), []).append((ri, desc))

        ja_preenchidos: list[dict] = []

        for grupo in grupos:
            reg      = grupo.get("REG")
            nome_raw = str(grupo.get("NOME") or grupo.get("nome") or "?")
            nome     = _title_grupo(nome_raw)
            area     = str(grupo.get("AREA")          or grupo.get("area")         or "").strip()
            vunit    = str(grupo.get("VALORUNIDADE")   or grupo.get("valorUnidade") or "").strip()
            obs      = str(grupo.get("OBS")            or grupo.get("obs")          or "").strip()
            constr   = grupo.get("CONSTRUCAO") or grupo.get("construcao") or "N"
            averb    = grupo.get("AVERBADO")   or grupo.get("averbado")   or "N"

            def _vazio(v: str) -> bool:
                if not v or v.lower() in ("null", "none", ""):
                    return True
                try:
                    return float(v.replace(",", ".")) == 0
                except (ValueError, TypeError):
                    return False

            tinha_dados = (
                not _vazio(area)
                or not _vazio(vunit)
                or obs.lower() not in ("", "não se aplica", "nao se aplica", "null", "none")
            )

            if tinha_dados:
                ja_preenchidos.append({"nome": nome, "area": area, "valor": vunit, "obs": obs})
                log(f"   '{nome}' sobrescrito com 'Não se aplica'")
            else:
                log(f"   '{nome}' marcado como 'Não se aplica'")

            self.editar_grupo(uuid, reg, nome=nome, construcao=constr, averbado=averb, nao_se_aplica=True)

            for reg_img, desc_atual in imagens_por_grupo.get(str(reg), []):
                try:
                    desc_nova = _normalizar_nome_foto(desc_atual) if desc_atual else _normalizar_nome_foto(nome)
                    if desc_atual != desc_nova:
                        self.editar_descricao_imagem(uuid, reg, reg_img, desc_nova)
                        log(f"     Imagem {reg_img}: '{desc_atual}' → '{desc_nova}'")
                except Exception as exc:
                    log(f"     Imagem {reg_img}: erro — {exc}")

        return ja_preenchidos
