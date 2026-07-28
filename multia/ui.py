"""
ui.py — Interface gráfica do MultiA PDF Analyzer.
JanelaParecer: janela modal com todas as opções do parecer (padrão e privativo).
App: janela principal com drag-and-drop, log em tempo real e botões de ação.
"""
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading, os, re, time

from .config import (OPCOES, OBS_OPCIONAIS, RISCO_PERGUNTAS,
                     JWT_PADRAO, JWT_PADRAO_MAIS, AUTH_BASIC, AUTH_BASIC_MAIS, _ENV_CARREGADO)
from .utils import (normalizar_area, _title_grupo, _normalizar_nome_foto, resource_path,
                    INFRA_ITENS, OPCOES_FONTE_CROQUI)
from .coordenadas import normalizar_pontos, gerar_kml_bytes, gerar_shp
from .gemini import chamar_gemini
from .prompts import PROMPT_NORMAL, PROMPT_ANTIGO
from .parecer import gerar_parecer
from .api import MultiaAPI
from .fotos import extrair_fotos_pdf, extrair_fotos_qrcode_pdf, obter_sessao_autenticada
from requests.exceptions import SSLError, ConnectionError as ReqConnError, Timeout

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

class JanelaParecer(ctk.CTkToplevel):
    def __init__(self,master,dados_pdf,on_confirmar):
        super().__init__(master)
        self.title("Opções do Parecer")
        self.geometry("820x950")
        self.resizable(True,True)
        self.minsize(700,800)
        self.dados_pdf=dados_pdf or {}
        self.on_confirmar=on_confirmar
        self._vars={}
        self._vars_risco=[]
        self._build()
        # Pré-selecionar Esquina de Quadra se Gemini identificou esquina
        _pos_gemini = (dados_pdf or {}).get("posicao","")
        if _pos_gemini and "esquina" in str(_pos_gemini).lower():
            self._vars["posicao"].set("Esquina de Quadra")
        _rua_esc = (dados_pdf or {}).get("rua_esquina","")
        if _rua_esc and "rua_esquina" in self._vars:
            self._vars["rua_esquina"].set(_rua_esc)
        # Pré-preencher campos do modelo privativo com dados do Gemini
        _unidade = (dados_pdf or {}).get("unidade","")
        if _unidade and "ap_num" in self._vars:
            self._vars["ap_num"].set(_unidade)
        _edificio = (dados_pdf or {}).get("complemento","")
        if _edificio and "ap_edificio" in self._vars:
            self._vars["ap_edificio"].set(_edificio)
        self.lift(); self.focus_force()

    def _sep(self,parent):
        ctk.CTkFrame(parent,height=1,fg_color="#dde3ec").pack(fill="x",pady=6)

    def _secao(self,parent,txt):
        ctk.CTkLabel(parent,text=txt,font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(10,4))

    def _build(self):
        topo=ctk.CTkFrame(self,fg_color="#f0f4fa")
        topo.pack(fill="x",padx=12,pady=(12,0))
        topo.columnconfigure(0,weight=1); topo.columnconfigure(1,weight=1); topo.columnconfigure(2,weight=1)

        # ── Modelo de Parecer ──
        ctk.CTkLabel(topo,text="Modelo de Parecer",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").grid(row=0,column=0,columnspan=3,sticky="w",padx=14,pady=(10,4))
        self._vars["modelo_parecer"]=ctk.StringVar(value="padrao")
        fm=ctk.CTkFrame(topo,fg_color="transparent"); fm.grid(row=1,column=0,columnspan=3,sticky="w",padx=14,pady=(0,10))
        ctk.CTkRadioButton(fm,text="📄 Modelo Padrão",variable=self._vars["modelo_parecer"],value="padrao",
                           fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",
                           command=self._on_modelo).pack(side="left",padx=(0,16))
        ctk.CTkRadioButton(fm,text="🏢 Modelo de Área Privativa",variable=self._vars["modelo_parecer"],value="privativo",
                           fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",
                           command=self._on_modelo).pack(side="left")

        ctk.CTkLabel(topo,text="Cooperativa",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").grid(row=2,column=0,sticky="w",padx=14,pady=(10,4))
        self._vars["coop"]=ctk.StringVar(value="outra")
        fc=ctk.CTkFrame(topo,fg_color="transparent"); fc.grid(row=3,column=0,sticky="w",padx=14,pady=(0,10))
        for val,lbl in [("outra","Outra"),("maxicredito","Sicoob Maxicrédito"),("biomas","Sicredi Biomas")]:
            ctk.CTkRadioButton(fc,text=lbl,variable=self._vars["coop"],value=val,
                               fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",
                               command=self._on_coop).pack(side="left",padx=(0,16))

        ctk.CTkLabel(topo,text="Validade",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").grid(row=2,column=1,sticky="w",padx=14,pady=(10,4))
        self._vars["validade"]=ctk.StringVar(value="12")
        fv=ctk.CTkFrame(topo,fg_color="transparent"); fv.grid(row=3,column=1,sticky="w",padx=14,pady=(0,10))
        self._rb_val={}
        for v,lbl in [("6","6 meses"),("12","12 meses"),("24","24 meses")]:
            rb=ctk.CTkRadioButton(fv,text=lbl,variable=self._vars["validade"],value=v,
                               fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e")
            rb.pack(side="left",padx=(0,12)); self._rb_val[v]=rb

        ctk.CTkFrame(self,height=1,fg_color="#dde3ec").pack(side="bottom",fill="x",padx=12)
        ctk.CTkButton(self,text="✅  Gerar e Salvar Parecer",height=50,
                      font=ctk.CTkFont(size=15,weight="bold"),
                      fg_color="#1F6AA5",hover_color="#144f7a",text_color="white",border_color="#f5c518",border_width=2,
                      command=self._confirmar).pack(side="bottom",fill="x",padx=12,pady=10)

        ctk.CTkFrame(self,height=1,fg_color="#dde3ec").pack(fill="x",padx=12,pady=(8,0))

        scroll=ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both",expand=True,padx=12,pady=4)

        # Container único filho do scroll — reduz recálculos de layout no scroll
        _container=ctk.CTkFrame(scroll,fg_color="transparent")
        _container.pack(fill="both",expand=True)

        # ── Painel Área Privativa (oculto por padrão) ──
        self._frame_privativo=ctk.CTkFrame(_container,fg_color="#eef4ff",border_color="#1a2c5b",border_width=1)
        ctk.CTkLabel(self._frame_privativo,text="Dados do Apartamento",
                     font=ctk.CTkFont(size=12,weight="bold"),text_color="#1a2c5b").pack(anchor="w",padx=12,pady=(10,4))
        _d=self.dados_pdf or {}

        # Nº do apartamento
        fr_ap1=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap1.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap1,text="Nº do Apartamento:",width=180,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_num"]=ctk.StringVar()
        ctk.CTkEntry(fr_ap1,textvariable=self._vars["ap_num"],width=100,placeholder_text="ex: 301",
                     text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        # Vagas de garagem
        fr_ap2=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap2.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap2,text="Qtd. de Vagas:",width=180,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_vagas_qtd"]=ctk.StringVar(value="0")
        self._ap_vagas_entries=[]
        _fr_vagas_num=ctk.CTkFrame(self._frame_privativo,fg_color="transparent")
        _fr_vagas_num.pack(fill="x",padx=12,pady=(0,4))
        def _atualizar_vagas(*_):
            for w in _fr_vagas_num.winfo_children(): w.destroy()
            self._ap_vagas_entries.clear()
            try: n=int(self._vars["ap_vagas_qtd"].get())
            except: n=0
            for i in range(n):
                fr_v=ctk.CTkFrame(_fr_vagas_num,fg_color="transparent"); fr_v.pack(side="left",padx=(0,8))
                ctk.CTkLabel(fr_v,text=f"Vaga {i+1} nº:",font=ctk.CTkFont(size=11)).pack(side="left",padx=(0,4))
                sv=ctk.StringVar()
                ctk.CTkEntry(fr_v,textvariable=sv,width=70,placeholder_text="ex: 12",
                             text_color="#1a2c5b",fg_color="white").pack(side="left")
                self._ap_vagas_entries.append(sv)
        _spin_frame=ctk.CTkFrame(fr_ap2,fg_color="transparent"); _spin_frame.pack(side="left",padx=4)
        for lbl_btn,delta in [("-",-1),("+",1)]:
            def _btn_cb(d=delta):
                try: v=int(self._vars["ap_vagas_qtd"].get())
                except: v=0
                self._vars["ap_vagas_qtd"].set(str(max(0,v+d))); _atualizar_vagas()
            ctk.CTkButton(_spin_frame,text=lbl_btn,width=30,height=28,
                          fg_color="#1a2c5b",hover_color="#144f7a",text_color="white",
                          command=_btn_cb).pack(side="left",padx=2)
        ctk.CTkLabel(_spin_frame,textvariable=self._vars["ap_vagas_qtd"],
                     width=24,font=ctk.CTkFont(size=12,weight="bold"),text_color="#1a2c5b").pack(side="left",padx=4)

        # Nome do edifício
        fr_ap3=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap3.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap3,text="Nome do Edifício:",width=180,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_edificio"]=ctk.StringVar()
        ctk.CTkEntry(fr_ap3,textvariable=self._vars["ap_edificio"],width=300,
                     placeholder_text="ex: La Victoria Residence",
                     text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        # Nome do empreendimento (parágrafo 2)
        ctk.CTkLabel(self._frame_privativo,text="Dados do Parágrafo 2 (empreendimento)",
                     font=ctk.CTkFont(size=11,weight="bold"),text_color="#1a2c5b").pack(anchor="w",padx=12,pady=(10,2))
        fr_ap4=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap4.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap4,text="Nome do Empreendimento:",width=200,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_empreendimento"]=ctk.StringVar()
        ctk.CTkEntry(fr_ap4,textvariable=self._vars["ap_empreendimento"],width=280,
                     placeholder_text="ex: La Victoria Residence",
                     text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        fr_ap5=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap5.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap5,text="Construtora:",width=200,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_construtora"]=ctk.StringVar()
        ctk.CTkEntry(fr_ap5,textvariable=self._vars["ap_construtora"],width=280,
                     placeholder_text="ex: KMK Empreendimentos",
                     text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        fr_ap6=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap6.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap6,text="Ano de Entrega:",width=200,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_ano"]=ctk.StringVar()
        ctk.CTkEntry(fr_ap6,textvariable=self._vars["ap_ano"],width=100,placeholder_text="ex: 2021",
                     text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        fr_ap7=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); fr_ap7.pack(fill="x",padx=12,pady=2)
        ctk.CTkLabel(fr_ap7,text="Distância do mar (opcional):",width=200,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars["ap_dist_mar"]=ctk.StringVar()
        ctk.CTkEntry(fr_ap7,textvariable=self._vars["ap_dist_mar"],width=150,
                     placeholder_text="ex: 170 metros",
                     text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        # Checklist de lazer
        ctk.CTkLabel(self._frame_privativo,text="Infraestrutura de Lazer:",
                     font=ctk.CTkFont(size=11,weight="bold"),text_color="#1a2c5b").pack(anchor="w",padx=12,pady=(8,2))
        _LAZER_ITEMS=[
            "piscina adulto e infantil","salão de festas","sala de jogos","playground",
            "hidromassagem","hall de entrada decorado","acesso controlado por biometria e tag",
            "box de praia privativo","elevador",
        ]
        self._vars["ap_lazer"]={item:ctk.BooleanVar() for item in _LAZER_ITEMS}
        _fr_lazer=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); _fr_lazer.pack(fill="x",padx=12)
        for i,item in enumerate(_LAZER_ITEMS):
            ctk.CTkCheckBox(_fr_lazer,text=item,variable=self._vars["ap_lazer"][item],
                            font=ctk.CTkFont(size=11)).grid(row=i//2,column=i%2,sticky="w",pady=2,padx=4)
        ctk.CTkLabel(self._frame_privativo,text="Outros itens de lazer (opcional):",
                     font=ctk.CTkFont(size=11),text_color="gray").pack(anchor="w",padx=12,pady=(6,2))
        self._vars["ap_lazer_livre"]=ctk.StringVar()
        ctk.CTkEntry(self._frame_privativo,textvariable=self._vars["ap_lazer_livre"],width=600,
                     placeholder_text="ex: academia, sauna",
                     text_color="#1a2c5b",fg_color="white").pack(anchor="w",padx=12,pady=(0,12))

        # infraestrutura e distâncias (área privativa também usa)
        _fr_ap_infra=ctk.CTkFrame(self._frame_privativo,fg_color="transparent"); _fr_ap_infra.pack(fill="x",padx=12,pady=(0,4))
        ctk.CTkLabel(_fr_ap_infra,text="Distância à Rodovia:",width=180,anchor="w",font=ctk.CTkFont(size=11)).pack(side="left")
        self._vars.setdefault("dist_rodovia_ap",ctk.StringVar())
        ctk.CTkEntry(_fr_ap_infra,textvariable=self._vars["dist_rodovia_ap"],width=120,
                     placeholder_text="ex: 5,3 km",text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)
        ctk.CTkLabel(_fr_ap_infra,text="Distância ao Centro:",font=ctk.CTkFont(size=11)).pack(side="left",padx=(16,4))
        self._vars.setdefault("dist_centro_ap",ctk.StringVar())
        ctk.CTkEntry(_fr_ap_infra,textvariable=self._vars["dist_centro_ap"],width=120,
                     placeholder_text="ex: 8,8 km",text_color="#1a2c5b",fg_color="white").pack(side="left",padx=4)

        # Container das 2 colunas de opções (grid interno)
        self._frame_colunas=ctk.CTkFrame(_container,fg_color="transparent")
        self._frame_colunas.pack(fill="x")
        self._frame_colunas.columnconfigure(0,weight=1); self._frame_colunas.columnconfigure(1,weight=1)

        ESQUERDA=["posicao","relevo","vegetacao","veg_extra","formato","solo","cercamento","calcada","meio_fio"]
        DIREITA  =["extras","tipo_via","pavimentacao","conservacao","tipo_rua","padrao","dist_rodovia","dist_centro"]

        col_a=ctk.CTkFrame(self._frame_colunas,fg_color="transparent")
        col_b=ctk.CTkFrame(self._frame_colunas,fg_color="transparent")
        col_a.grid(row=0,column=0,sticky="nsew",padx=(0,6))
        col_b.grid(row=0,column=1,sticky="nsew",padx=(6,0))

        def render_op(parent,op):
            # dist_centro tem seu próprio label renderizado inline — não duplicar
            if op["id"] != "dist_centro":
                self._secao(parent,op["label"])
            if op["tipo"]=="relevo_custom":
                # ── Relevo em 2 níveis ──
                self._vars["relevo_mod"]  = ctk.StringVar(value="Totalmente")
                self._vars["relevo_dir"]  = ctk.StringVar(value="Em Aclive")
                self._vars["relevo_comp"] = ctk.StringVar(value="Em Aclive")
                _MODS  = ["Totalmente","Parcialmente","Predominantemente"]
                _DIRS  = ["Em Aclive","Plano","Em Declive"]
                # Nível 1
                fr_mod = ctk.CTkFrame(parent,fg_color="transparent")
                fr_mod.pack(anchor="w",padx=8,pady=(0,4))
                for m in _MODS:
                    ctk.CTkRadioButton(fr_mod,text=m,variable=self._vars["relevo_mod"],value=m,
                                       font=ctk.CTkFont(size=11),fg_color="#f5c518",
                                       border_color="#1a2c5b",hover_color="#d4a80e",
                                       command=self._on_relevo).pack(side="left",padx=(0,8))
                # Nível 2 — principal
                ctk.CTkLabel(parent,text="Terreno:",font=ctk.CTkFont(size=11),
                             text_color="gray").pack(anchor="w",padx=8)
                fr_dir = ctk.CTkFrame(parent,fg_color="transparent")
                fr_dir.pack(anchor="w",padx=8,pady=(0,2))
                for d in _DIRS:
                    ctk.CTkRadioButton(fr_dir,text=d,variable=self._vars["relevo_dir"],value=d,
                                       font=ctk.CTkFont(size=11),fg_color="#f5c518",
                                       border_color="#1a2c5b",hover_color="#d4a80e").pack(side="left",padx=(0,8))
                # Nível 3 — complemento (oculto quando Totalmente)
                self._lbl_comp = ctk.CTkLabel(parent,text="Com áreas:",font=ctk.CTkFont(size=11),
                                              text_color="gray")
                self._fr_comp  = ctk.CTkFrame(parent,fg_color="transparent")
                for d in _DIRS:
                    ctk.CTkRadioButton(self._fr_comp,text=d,variable=self._vars["relevo_comp"],value=d,
                                       font=ctk.CTkFont(size=11),fg_color="#f5c518",
                                       border_color="#1a2c5b",hover_color="#d4a80e").pack(side="left",padx=(0,8))
                self._on_relevo()  # estado inicial
            elif op["tipo"]=="radio":
                self._vars[op["id"]]=ctk.StringVar(value=op["opcoes"][0])
                for opc in op["opcoes"]:
                    # Cada radio fica numa linha horizontal para permitir campos inline
                    _row=ctk.CTkFrame(parent,fg_color="transparent")
                    _row.pack(anchor="w",fill="x",padx=8,pady=1)
                    rb=ctk.CTkRadioButton(_row,text=opc,variable=self._vars[op["id"]],value=opc,
                                       font=ctk.CTkFont(size=11),fg_color="#f5c518",border_color="#1a2c5b",hover_color="#d4a80e")
                    rb.pack(side="left")
                    # Campo "Rua que faz esquina" inline com o radio Esquina de Quadra
                    if op["id"]=="posicao" and opc=="Esquina de Quadra":
                        self._frame_rua_esquina=ctk.CTkFrame(_row,fg_color="transparent")
                        ctk.CTkLabel(self._frame_rua_esquina,text="Nome da rua que faz esquina:",
                                     font=ctk.CTkFont(size=11),
                                     text_color="#1a2c5b").pack(side="left",padx=(12,4))
                        self._vars["rua_esquina"]=ctk.StringVar()
                        ctk.CTkEntry(self._frame_rua_esquina,textvariable=self._vars["rua_esquina"],
                                     placeholder_text="ex: Rua das Flores",width=220,
                                     text_color="#1a2c5b",fg_color="white").pack(side="left")
                        self._frame_rua_esquina.pack_forget()
                    # Campo "Nome da Rodovia" inline com o radio Rodovia (Às Margens)
                    if op["id"]=="tipo_via" and opc=="Rodovia (Às Margens)":
                        self._frame_rodovia_nome=ctk.CTkFrame(_row,fg_color="transparent")
                        ctk.CTkLabel(self._frame_rodovia_nome,text="Nome da rodovia:",
                                     font=ctk.CTkFont(size=11),
                                     text_color="#1a2c5b").pack(side="left",padx=(12,4))
                        self._vars["rodovia_nome"]=ctk.StringVar()
                        ctk.CTkEntry(self._frame_rodovia_nome,textvariable=self._vars["rodovia_nome"],
                                     placeholder_text="ex: BR-116",width=200,
                                     text_color="#1a2c5b",fg_color="white").pack(side="left")
                        self._frame_rodovia_nome.pack_forget()
                # Listeners para mostrar/ocultar campos extras
                if op["id"]=="posicao":
                    def _on_posicao(*_):
                        if self._vars["posicao"].get()=="Esquina de Quadra":
                            self._frame_rua_esquina.pack(side="left")
                        else:
                            self._frame_rua_esquina.pack_forget()
                    self._vars["posicao"].trace_add("write",_on_posicao)
                if op["id"]=="tipo_via":
                    def _on_via(*_):
                        eh_margem = self._vars["tipo_via"].get()=="Rodovia (Às Margens)"
                        if eh_margem:
                            self._frame_rodovia_nome.pack(side="left")
                        else:
                            self._frame_rodovia_nome.pack_forget()
                        # Ocultar/mostrar campo de distância até a rodovia
                        if hasattr(self,"_frame_dist_rodovia"):
                            if eh_margem:
                                self._frame_dist_rodovia.pack_forget()
                            else:
                                self._frame_dist_rodovia.pack(anchor="w",padx=8,pady=2)
                    self._vars["tipo_via"].trace_add("write",_on_via)
            elif op["tipo"]=="checkbox":
                self._vars[op["id"]]={opc:ctk.BooleanVar() for opc in op["opcoes"]}
                for opc,v in self._vars[op["id"]].items():
                    ctk.CTkCheckBox(parent,text=opc,variable=v,
                                    font=ctk.CTkFont(size=11)).pack(anchor="w",padx=8,pady=1)
            elif op["tipo"]=="texto":
                self._vars[op["id"]]=ctk.StringVar()
                if op["id"]=="dist_centro":
                    # dist_centro e nome_rodovia ficam na mesma linha que dist_rodovia
                    # Criar frame horizontal compartilhado se ainda não existe
                    if not hasattr(self,"_frame_textos_linha"):
                        self._frame_textos_linha=ctk.CTkFrame(parent,fg_color="transparent")
                        self._frame_textos_linha.pack(anchor="w",fill="x")
                    _entry_frame=ctk.CTkFrame(self._frame_textos_linha,fg_color="transparent")
                    _entry_frame.pack(side="left",padx=(0,20))
                    self._secao(_entry_frame,op["label"])
                    ctk.CTkEntry(_entry_frame,textvariable=self._vars[op["id"]],
                                 placeholder_text=op.get("placeholder",""),
                                 width=160).pack(anchor="w",padx=8,pady=2)
                else:
                    _entry_frame=ctk.CTkFrame(parent,fg_color="transparent")
                    _entry_frame.pack(anchor="w",fill="x")
                    ctk.CTkEntry(_entry_frame,textvariable=self._vars[op["id"]],
                                 placeholder_text=op.get("placeholder",""),
                                 width=220).pack(anchor="w",padx=8,pady=2)
                if op["id"]=="dist_rodovia":
                    self._frame_dist_rodovia=_entry_frame

        for op in OPCOES:
            if op["id"] in ESQUERDA: render_op(col_a,op)
            elif op["id"] in DIREITA: render_op(col_b,op)

        self._frame_risco=ctk.CTkFrame(_container,border_color="#ef4444",border_width=2,fg_color="#2d0a0a")
        self._frame_risco.pack_forget()
        ctk.CTkLabel(self._frame_risco,text="⚠️  Risco Socioambiental",
                     font=ctk.CTkFont(size=12,weight="bold"),text_color="#ef4444").pack(anchor="w",padx=12,pady=(8,4))
        for q in RISCO_PERGUNTAS:
            ctk.CTkLabel(self._frame_risco,text=q,wraplength=700,justify="left",
                         font=ctk.CTkFont(size=11),text_color="#f0f0f0").pack(anchor="w",padx=12,pady=(4,0))
            v=ctk.StringVar(value="Não")
            fr=ctk.CTkFrame(self._frame_risco,fg_color="transparent"); fr.pack(anchor="w",padx=24,pady=(2,4))
            for opt,cor in [("Não","#3b82f6"),("Sim","#ef4444"),("Inconclusivo","#f59e0b")]:
                ctk.CTkRadioButton(fr,text=opt,variable=v,value=opt,fg_color="#f5c518",border_color="#ef4444",hover_color="#d4a80e",text_color="#f0f0f0").pack(side="left",padx=(0,16))
            self._vars_risco.append(v)
        ctk.CTkLabel(self._frame_risco,text="Observações:",font=ctk.CTkFont(size=11),text_color="#f0f0f0").pack(anchor="w",padx=12)
        self._vars["risco_obs"]=ctk.StringVar(value="Não aplicável.")
        ctk.CTkEntry(self._frame_risco,textvariable=self._vars["risco_obs"],fg_color="#1a1a1a",text_color="#f0f0f0",border_color="#ef4444").pack(fill="x",padx=12,pady=(0,10))

        # ── Coordenadas + Fonte do Croqui ──
        fextra=ctk.CTkFrame(_container,fg_color="transparent")
        fextra.pack(fill="x",pady=(8,0))

        ctk.CTkLabel(fextra,text="Coordenadas do Imóvel",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(10,4))
        ctk.CTkLabel(fextra,text="Cole aqui a coordenada coletada em campo (será salva na capa):",
                     font=ctk.CTkFont(size=11),text_color="gray").pack(anchor="w",padx=8)
        self._vars["coordenadas_manual"]=ctk.StringVar(
            value=self.dados_pdf.get("coordenadas_raw") or "")
        ctk.CTkEntry(fextra,textvariable=self._vars["coordenadas_manual"],
                     placeholder_text="ex: -27.123456, -50.654321",
                     width=400).pack(anchor="w",padx=8,pady=(2,10))

        ctk.CTkLabel(fextra,text="Fonte do Croqui",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(6,4))
        fonte_pdf=self.dados_pdf.get("fonte_croqui") or OPCOES_FONTE_CROQUI[0]
        _fonte_inicial=next((o for o in OPCOES_FONTE_CROQUI
                             if o.lower()[:30] in (fonte_pdf or "").lower()), OPCOES_FONTE_CROQUI[0])
        self._vars["fonte_croqui"]=ctk.StringVar(value=_fonte_inicial)
        ctk.CTkComboBox(fextra,variable=self._vars["fonte_croqui"],
                        values=OPCOES_FONTE_CROQUI,
                        width=760,dropdown_font=ctk.CTkFont(size=11),
                        font=ctk.CTkFont(size=11)).pack(anchor="w",padx=8,pady=(2,10))

        # ── Infraestrutura ──
        finfra=ctk.CTkFrame(_container,fg_color="transparent")
        finfra.pack(fill="x",pady=(8,0))
        ctk.CTkLabel(finfra,text="Infraestrutura da Região",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(10,4))
        ctk.CTkLabel(finfra,text="Itens marcados serão incluídos no parecer:",
                     font=ctk.CTkFont(size=11),text_color="gray").pack(anchor="w",padx=8,pady=(0,4))
        self._vars["infra"]={item:ctk.BooleanVar(value=True) for item in INFRA_ITENS}
        for item in INFRA_ITENS:
            ctk.CTkCheckBox(finfra,text=item,variable=self._vars["infra"][item],
                            font=ctk.CTkFont(size=11)).pack(anchor="w",padx=8,pady=2)

        fobs=ctk.CTkFrame(_container,fg_color="transparent")
        fobs.pack(fill="x",pady=(8,0))
        ctk.CTkLabel(fobs,text="Observações opcionais",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(10,4))
        self._vars["obs_extras"]={i:ctk.BooleanVar() for i in range(len(OBS_OPCIONAIS))}
        for i,obs_txt in enumerate(OBS_OPCIONAIS):
            row=ctk.CTkFrame(fobs,fg_color="transparent"); row.pack(fill="x",padx=4,pady=2)
            cb=ctk.CTkCheckBox(row,text="",variable=self._vars["obs_extras"][i],width=24)
            cb.pack(side="left",padx=(0,4))
            ctk.CTkLabel(row,text=obs_txt,wraplength=700,justify="left",
                         font=ctk.CTkFont(size=11)).pack(side="left",anchor="w")
        ctk.CTkLabel(fobs,text="Observação adicional:",font=ctk.CTkFont(size=11,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",padx=4,pady=(8,2))
        ctk.CTkLabel(fobs,text="Será adicionada como última observação (deixe em branco para omitir).",
                     font=ctk.CTkFont(size=11),text_color="gray").pack(anchor="w",padx=4)
        self._vars["obs_adicional"]=ctk.StringVar()
        ctk.CTkEntry(fobs,textvariable=self._vars["obs_adicional"],
                     placeholder_text="Digite aqui a observação adicional...",
                     width=720,text_color="#1a2c5b",fg_color="white").pack(anchor="w",padx=4,pady=(2,8))

        # ── Divisões de Área de Terras ──
        self._frame_divisoes_container=ctk.CTkFrame(_container,fg_color="transparent")
        self._frame_divisoes_container.pack(fill="x",pady=(8,0))
        farea=ctk.CTkFrame(self._frame_divisoes_container,fg_color="transparent")
        farea.pack(fill="x")
        DIVISOES_AREA = [
            "Área Útil",
            "Área de Preservação Permanente",
            "Área de Reserva Legal",
            "Área de Mata Nativa",
            "Área Mecanizada",
            "Área Mecanizável",
            "Área de Reflorestamento",
            "Área Não Edificável",
        ]
        DIVISOES_PADRAO_ATIVO = {"Área Útil"}
        ctk.CTkLabel(farea,text="A área de terras tem quais divisões?",
                     font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(10,4))
        ctk.CTkLabel(farea,text="Selecione as divisões presentes. Se mais de uma, informe a área de cada uma:",
                     font=ctk.CTkFont(size=11),text_color="gray").pack(anchor="w",padx=8,pady=(0,6))

        self._vars_divisoes={}  # {nome: {"ativo":BooleanVar, "valor":StringVar, "entry":widget}}
        self._frame_divisoes_entradas=ctk.CTkFrame(farea,fg_color="transparent")

        # área total do PDF para cálculo automático
        _area_total_pdf=(self.dados_pdf or {}).get("area_total","")

        def _atualizar_entradas():
            ativas=[n for n,inf in self._vars_divisoes.items() if inf["ativo"].get()]
            # mostrar/ocultar frame de entradas
            if len(ativas)>1:
                self._frame_divisoes_entradas.pack(fill="x",padx=8,pady=(4,0))
            else:
                self._frame_divisoes_entradas.pack_forget()
            # mostrar só entradas das ativas
            for nome,inf in self._vars_divisoes.items():
                if inf["row_frame"].winfo_ismapped() if len(ativas)>1 else False:
                    pass
                if len(ativas)>1 and inf["ativo"].get():
                    inf["row_frame"].pack(fill="x",pady=2)
                else:
                    inf["row_frame"].pack_forget()
            _recalcular()

        def _recalcular(*_):
            ativas=[n for n,inf in self._vars_divisoes.items() if inf["ativo"].get()]
            if len(ativas)<=1: return
            import re as _re
            def _tof(v):
                n=_re.sub(r"[^\d,.]","",str(v)).strip()
                if not n or n in (",","."): return None
                if _re.match(r"^\d{1,3}(\.\d{3})+,\d+$",n): n=n.replace(".","").replace(",",".")
                else: n=n.replace(",",".")
                try: return float(n)
                except: return None
            total=_tof(_area_total_pdf)
            if not total: return
            # campo vazio = sem valor; campo com valor inválido/incompleto = não conta como preenchido
            vazios=[n for n in ativas if _tof(self._vars_divisoes[n]["valor"].get()) is None]
            preenchidos=[n for n in ativas if _tof(self._vars_divisoes[n]["valor"].get()) is not None]
            if len(vazios)==1:
                soma=sum(_tof(self._vars_divisoes[n]["valor"].get()) or 0 for n in preenchidos)
                restante=total-soma
                if restante>=0:
                    self._vars_divisoes[vazios[0]]["valor"].set(f"{restante:.4f}".replace(".",","))
                    self._vars_divisoes[vazios[0]]["entry"].configure(text_color="#1a2c5b")
            # resetar cor das entradas editadas manualmente
            for n in preenchidos:
                self._vars_divisoes[n]["entry"].configure(text_color="#1a2c5b")

        # criar checkboxes
        for nome in DIVISOES_AREA:
            ativo=ctk.BooleanVar(value=(nome in DIVISOES_PADRAO_ATIVO))
            valor=ctk.StringVar()
            cb_row=ctk.CTkFrame(farea,fg_color="transparent")
            cb_row.pack(fill="x",padx=4,pady=1)
            ctk.CTkCheckBox(cb_row,text=nome,variable=ativo,font=ctk.CTkFont(size=11),
                            command=_atualizar_entradas).pack(side="left",padx=8)
            self._vars_divisoes[nome]={"ativo":ativo,"valor":valor,"row_frame":None,"entry":None}

        # criar entradas (ocultas inicialmente) dentro do frame de entradas
        self._frame_divisoes_entradas.pack_forget()
        for nome in DIVISOES_AREA:
            row_e=ctk.CTkFrame(self._frame_divisoes_entradas,fg_color="transparent")
            ctk.CTkLabel(row_e,text=f"{nome}:",width=220,anchor="w",
                         font=ctk.CTkFont(size=11)).pack(side="left",padx=(4,4))
            entry=ctk.CTkEntry(row_e,textvariable=self._vars_divisoes[nome]["valor"],
                               width=130,placeholder_text="ex: 1,3000",
                               text_color="#1a2c5b",fg_color="white")
            entry.pack(side="left",padx=(0,6))
            entry.bind("<FocusOut>", lambda e: _recalcular())
            entry.bind("<Return>", lambda e: _recalcular())
            self._vars_divisoes[nome]["row_frame"]=row_e
            self._vars_divisoes[nome]["entry"]=entry

        # ── Descrição do Acesso ──
        fac_desc=ctk.CTkFrame(_container,fg_color="transparent")
        fac_desc.pack(fill="x",pady=(8,0))
        ctk.CTkLabel(fac_desc,text="Descrição do Acesso",font=ctk.CTkFont(size=12,weight="bold"),
                     text_color="#1a2c5b").pack(anchor="w",pady=(10,4))
        ctk.CTkLabel(fac_desc,text="Características adicionais do acesso (opcional):",
                     font=ctk.CTkFont(size=11),text_color="gray").pack(anchor="w",padx=8,pady=(0,4))
        _ACESSO_CHECKS = [
            "Passa por ponte de madeira",
            "Passa por ponte de concreto",
            "Acesso compartilhado com outros imóveis",
        ]
        self._vars["acesso_checks"]={item:ctk.BooleanVar() for item in _ACESSO_CHECKS}
        for item in _ACESSO_CHECKS:
            ctk.CTkCheckBox(fac_desc,text=item,variable=self._vars["acesso_checks"][item],
                            font=ctk.CTkFont(size=11)).pack(anchor="w",padx=8,pady=2)
        ctk.CTkLabel(fac_desc,text="Descreva o acesso (ex: passa por ponte, etc...):",
                     font=ctk.CTkFont(size=11),text_color="#1a2c5b").pack(anchor="w",padx=8,pady=(6,2))
        self._vars["acesso_livre"]=ctk.StringVar()
        ctk.CTkEntry(fac_desc,textvariable=self._vars["acesso_livre"],
                     placeholder_text="Ex: sendo necessário autorização do proprietário vizinho",
                     width=720,text_color="#1a2c5b",fg_color="white").pack(anchor="w",padx=8,pady=(0,8))

        # ── Uso Atual das Terras (somente RURAL) ──
        tipo_imovel_build=(self.dados_pdf or {}).get("tipo_imovel","URBANO").upper()
        if tipo_imovel_build=="RURAL":
            fuso=ctk.CTkFrame(_container,fg_color="#eef4ff",border_color="#1a2c5b",border_width=1)
            fuso.pack(fill="x",pady=(8,0))
            ctk.CTkLabel(fuso,text="Uso atual das terras",font=ctk.CTkFont(size=12,weight="bold"),
                         text_color="#1a2c5b").pack(anchor="w",padx=12,pady=(10,4))
            _USOS_TERRAS=[
                "Pastagem","Lavoura","Dupla Aptidão (Pastagem e Lavoura)",
                "Agroindustrial","Mineração","Fruticultura ou Parreiral","Granjas Suínas ou Aviários",
            ]
            self._vars["uso_terras"]=ctk.StringVar(value=_USOS_TERRAS[0])
            for u in _USOS_TERRAS:
                ctk.CTkRadioButton(fuso,text=u,variable=self._vars["uso_terras"],value=u,
                                   font=ctk.CTkFont(size=11),fg_color="#f5c518",
                                   border_color="#1a2c5b",hover_color="#d4a80e").pack(anchor="w",padx=12,pady=2)
            ctk.CTkFrame(fuso,height=8,fg_color="transparent").pack()

        self._on_coop()

    def _on_modelo(self,*_):
        modelo=self._vars["modelo_parecer"].get()
        if modelo=="privativo":
            self._frame_privativo.pack(fill="x",padx=12,pady=(0,8))
            self._frame_colunas.pack_forget()
            self._frame_divisoes_container.pack_forget()
        else:
            self._frame_privativo.pack_forget()
            self._frame_colunas.pack(fill="x")
            self._frame_divisoes_container.pack(fill="x",pady=(8,0))

    def _on_relevo(self,*_):
        mod = self._vars["relevo_mod"].get()
        if mod == "Totalmente":
            self._lbl_comp.pack_forget()
            self._fr_comp.pack_forget()
        else:
            self._lbl_comp.pack(anchor="w",padx=8,pady=(4,0))
            self._fr_comp.pack(anchor="w",padx=8,pady=(0,2))

    def _on_coop(self):
        coop=self._vars["coop"].get()
        if coop!="outra":
            self._frame_risco.pack(fill="x",pady=6)
            val="24" if coop=="maxicredito" else "12"
            self._vars["validade"].set(val)
            for v,rb in self._rb_val.items():
                rb.configure(text_color="#333333" if v!=val else "#1a2c5b")
        else:
            self._frame_risco.pack_forget()

    def _confirmar(self):
        e={}
        for op in OPCOES:
            if op["tipo"] in ("radio","relevo_custom"):
                if op["tipo"]=="relevo_custom":
                    pass  # relevo montado abaixo
                else:
                    e[op["id"]]=self._vars[op["id"]].get()
            elif op["tipo"]=="checkbox":
                sels=[opc for opc,v in self._vars[op["id"]].items() if v.get()]
                e[op["id"]]=", ".join(sels) if sels else ""
            elif op["tipo"]=="texto": e[op["id"]]=self._vars[op["id"]].get().strip()
        # Relevo composto
        mod  = self._vars.get("relevo_mod",ctk.StringVar()).get()
        dire = self._vars.get("relevo_dir",ctk.StringVar()).get()
        comp = self._vars.get("relevo_comp",ctk.StringVar()).get()
        if mod=="Totalmente":
            e["relevo_texto"] = f"totalmente {dire.lower()}"
        else:
            e["relevo_texto"] = f"{mod.lower()} {dire.lower()}, com áreas {comp.lower()}"
        e["coop"]=self._vars["coop"].get()
        e["validade"]=self._vars["validade"].get()
        e["risco"]=[v.get() for v in self._vars_risco]
        e["risco_obs"]=self._vars["risco_obs"].get()
        e["obs_extras"]=[i for i,v in self._vars["obs_extras"].items() if v.get()]
        e["obs_adicional"]=self._vars.get("obs_adicional",ctk.StringVar()).get().strip()
        e["infra"]=[item for item,v in self._vars["infra"].items() if v.get()]
        e["coordenadas_manual"]=self._vars["coordenadas_manual"].get().strip()
        e["fonte_croqui"]=self._vars["fonte_croqui"].get().strip()
        e["divisoes_area"]={nome:inf["valor"].get().strip()
                            for nome,inf in self._vars_divisoes.items() if inf["ativo"].get()}
        e["rua_esquina"]=self._vars.get("rua_esquina",ctk.StringVar()).get().strip()
        e["rodovia_nome"]=self._vars.get("rodovia_nome",ctk.StringVar()).get().strip()
        # Descrição do acesso
        _checks_ativos=[item for item,v in self._vars["acesso_checks"].items() if v.get()]
        e["acesso_checks"]=_checks_ativos
        e["acesso_livre"]=self._vars.get("acesso_livre",ctk.StringVar()).get().strip()
        # Uso das terras (rural)
        e["uso_terras"]=self._vars.get("uso_terras",ctk.StringVar()).get().strip()
        # Modelo de parecer
        e["modelo_parecer"]=self._vars.get("modelo_parecer",ctk.StringVar()).get()
        # Campos área privativa
        e["ap_num"]=self._vars.get("ap_num",ctk.StringVar()).get().strip()
        e["ap_vagas_qtd"]=self._vars.get("ap_vagas_qtd",ctk.StringVar()).get().strip()
        e["ap_vagas_nums"]=[]
        for _sv in self._ap_vagas_entries:
            e["ap_vagas_nums"].append(_sv.get().strip())
        e["ap_edificio"]=self._vars.get("ap_edificio",ctk.StringVar()).get().strip()
        e["ap_empreendimento"]=self._vars.get("ap_empreendimento",ctk.StringVar()).get().strip()
        e["ap_construtora"]=self._vars.get("ap_construtora",ctk.StringVar()).get().strip()
        e["ap_ano"]=self._vars.get("ap_ano",ctk.StringVar()).get().strip()
        e["ap_dist_mar"]=self._vars.get("ap_dist_mar",ctk.StringVar()).get().strip()
        e["ap_lazer"]=[item for item,v in self._vars.get("ap_lazer",{}).items() if v.get()]
        e["ap_lazer_livre"]=self._vars.get("ap_lazer_livre",ctk.StringVar()).get().strip()
        e["dist_rodovia_ap"]=self._vars.get("dist_rodovia_ap",ctk.StringVar()).get().strip()
        e["dist_centro_ap"]=self._vars.get("dist_centro_ap",ctk.StringVar()).get().strip()
        e["dist_rodovia_ap"]=self._vars.get("dist_rodovia_ap",ctk.StringVar()).get().strip()
        e["dist_centro_ap"]=self._vars.get("dist_centro_ap",ctk.StringVar()).get().strip()
        self.destroy()
        self.on_confirmar(e)

# ──────────────────────────────────────────────────────────────
# APP PRINCIPAL
# ──────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD._require(self)
        except Exception:
            pass
        try:
            self.iconbitmap(resource_path("logo.ico"))
        except Exception:
            pass
        self.configure(fg_color="#d6e4f5")
        self.title("MultiA PDF Analyzer v2.0"); self.geometry("900x900"); self.resizable(True,True); self.minsize(640,600)
        self.jwt_token=None; self.pdf_path=None; self.dados_pdf=None
        self.api=None; self.uuid_atual=None
        import queue
        self._log_queue = queue.Queue()
        self._build_ui()
        self._conectar_auto()
        self.after(16, self._log_flush)  # loop 60Hz

    def _build_ui(self):
        # Container com scroll manual via Canvas — compatível com drag and drop
        # CTkScrollableFrame bloqueia DnD por criar um canvas interceptador
        self._canvas=tk.Canvas(self,bg="#d6e4f5",highlightthickness=0)
        self._vsb=ctk.CTkScrollbar(self,command=self._canvas.yview)
        self._vsb.pack(side="right",fill="y")
        self._canvas.pack(side="left",fill="both",expand=True)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        _c=ctk.CTkFrame(self._canvas,fg_color="#d6e4f5")
        self._canvas_window=self._canvas.create_window((0,0),window=_c,anchor="nw")
        def _on_frame_configure(e):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        def _on_canvas_configure(e):
            self._canvas.itemconfig(self._canvas_window,width=e.width)
        _c.bind("<Configure>",_on_frame_configure)
        self._canvas.bind("<Configure>",_on_canvas_configure)
        def _on_mousewheel(e):
            self._canvas.yview_scroll(int(-1*(e.delta/120)),"units")
        self._canvas.bind_all("<MouseWheel>",_on_mousewheel)
        # Registrar DnD no canvas para funcionar em toda a área da janela
        try:
            from tkinterdnd2 import DND_FILES
            self._canvas.drop_target_register(DND_FILES)
            self._canvas.dnd_bind("<<Drop>>",self._on_drop)
        except Exception:
            pass

        # ── Título centralizado ──
        _titulo_outer=ctk.CTkFrame(_c,fg_color="transparent")
        _titulo_outer.pack(pady=(18,4))
        _titulo_row=ctk.CTkFrame(_titulo_outer,fg_color="transparent")
        _titulo_row.pack()
        ctk.CTkLabel(_titulo_row,text="Multi",font=ctk.CTkFont(family="Georgia",size=34,weight="bold"),text_color="#1a2c5b").pack(side="left")
        ctk.CTkLabel(_titulo_row,text="A",font=ctk.CTkFont(family="Georgia",size=34,weight="bold"),text_color="#f5c518").pack(side="left")
        ctk.CTkLabel(_titulo_outer,text="PDF Analyzer",font=ctk.CTkFont(size=12),text_color="#6b8cba").pack()

        # ── Layout: esquerda (col0) = Sistema em cima + Buscar embaixo | direita (col1) = Laudo+PDF maior ──
        top=ctk.CTkFrame(_c,fg_color="transparent")
        top.pack(fill="x",padx=18,pady=(10,8))
        top.columnconfigure(0,weight=1); top.columnconfigure(1,weight=1)

        # Coluna esquerda — Sistema (topo) + Buscar (baixo)
        col_esq=ctk.CTkFrame(top,fg_color="transparent")
        col_esq.grid(row=0,column=0,sticky="nsew",padx=(0,6))

        fa=ctk.CTkFrame(col_esq,fg_color="#1a2c5b"); fa.pack(fill="x",pady=(0,6))
        ctk.CTkLabel(fa,text="1. SISTEMA",font=ctk.CTkFont(weight="bold"),text_color="white").pack(anchor="w",padx=14,pady=(8,4))
        rs=ctk.CTkFrame(fa,fg_color="transparent"); rs.pack(fill="x",padx=14,pady=(0,4))
        self.var_sistema=ctk.StringVar(value="multia")
        ctk.CTkRadioButton(rs,text="MultiA",variable=self.var_sistema,value="multia",
                           fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",text_color="white",
                           command=self._conectar_auto).pack(side="left",padx=(0,10))
        ctk.CTkRadioButton(rs,text="MultiA Mais",variable=self.var_sistema,value="multiamais",
                           fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",text_color="white",
                           command=self._conectar_auto).pack(side="left")
        self.lbl_auth=ctk.CTkLabel(fa,text="✅ Autenticado — MultiA",text_color="#4ade80",font=ctk.CTkFont(size=11))
        self.lbl_auth.pack(anchor="w",padx=14,pady=(4,8))

        fm=ctk.CTkFrame(col_esq,fg_color="#1a2c5b"); fm.pack(fill="x")
        ctk.CTkLabel(fm,text="3. BUSCAR IMÓVEL (INFOEL)",font=ctk.CTkFont(weight="bold"),text_color="white").pack(anchor="w",padx=14,pady=(8,4))
        ctk.CTkLabel(fm,text="Cód. Infoel:",text_color="white",font=ctk.CTkFont(size=11)).pack(anchor="w",padx=14)
        rm=ctk.CTkFrame(fm,fg_color="transparent"); rm.pack(fill="x",padx=14,pady=(4,8))
        self.entry_mat=ctk.CTkEntry(rm,placeholder_text="ex: 6362",fg_color="#d6e4f5",text_color="#1a2c5b",border_color="#f5c518"); self.entry_mat.pack(side="left",padx=(0,8),fill="x",expand=True)
        ctk.CTkButton(rm,text="🔍 Buscar",command=self._buscar,width=90,
                      fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b").pack(side="left")
        self.lbl_av=ctk.CTkLabel(fm,text="",text_color="#4ade80",font=ctk.CTkFont(size=11)); self.lbl_av.pack(anchor="w",padx=14,pady=(0,8))

        # Coluna direita — Laudo + PDF + botão Analisar (maior)
        fp=ctk.CTkFrame(top,fg_color="#1a2c5b"); fp.grid(row=0,column=1,sticky="nsew")
        ctk.CTkLabel(fp,text="2. OPÇÃO DE LAUDO + PDF",font=ctk.CTkFont(weight="bold"),text_color="white").pack(anchor="w",padx=14,pady=(8,4))
        rt=ctk.CTkFrame(fp,fg_color="transparent"); rt.pack(fill="x",padx=14,pady=(0,6))
        self.var_tipo=ctk.StringVar(value="normal")
        ctk.CTkRadioButton(rt,text="📄 Laudo Normal",variable=self.var_tipo,value="normal",fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",text_color="white").pack(side="left",padx=(0,12))
        ctk.CTkRadioButton(rt,text="📋 Laudo Antigo",variable=self.var_tipo,value="antigo",fg_color="#f5c518",border_color="#f5c518",hover_color="#d4a80e",text_color="white").pack(side="left")
        self.drop_frame=ctk.CTkFrame(fp,height=90,fg_color="#d6e4f5",border_color="#f5c518",border_width=2,cursor="hand2")
        self.drop_frame.pack(fill="x",padx=14,pady=(0,6))
        self.drop_frame.pack_propagate(False)
        self.lbl_pdf=ctk.CTkLabel(self.drop_frame,text="☁️   Arraste o PDF aqui  ou  clique para selecionar",
                                   text_color="#1a2c5b",font=ctk.CTkFont(size=12))
        self.lbl_pdf.pack(expand=True)
        self.drop_frame.bind("<Button-1>",lambda e: self._sel_pdf())
        self.lbl_pdf.bind("<Button-1>",lambda e: self._sel_pdf())
        self.btn_analisar=ctk.CTkButton(fp,text="🤖  Analisar PDF com Gemini",command=self._analisar,
                                        height=40,font=ctk.CTkFont(size=13,weight="bold"),
                                        fg_color="#f5c518",hover_color="#d4a80e",
                                        text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_analisar.pack(fill="x",padx=14,pady=(0,10))
        self._setup_dragdrop()

        # ── Andamento do processo ──
        fl=ctk.CTkFrame(_c,fg_color="#1a2c5b"); fl.pack(fill="x",padx=18,pady=(0,8))
        ctk.CTkLabel(fl,text="O QUE ESTÁ ACONTECENDO",font=ctk.CTkFont(weight="bold"),text_color="white").pack(anchor="w",padx=14,pady=(8,4))
        self.txt_log=ctk.CTkTextbox(fl,height=180,font=ctk.CTkFont(family="Courier",size=13),fg_color="#d6e4f5",text_color="#1a2c5b")
        self.txt_log.pack(fill="x",padx=14,pady=(0,10))

        fac=ctk.CTkFrame(_c,fg_color="#1a2c5b"); fac.pack(fill="x",padx=18,pady=(0,14))
        ctk.CTkLabel(fac,text="AÇÕES",font=ctk.CTkFont(weight="bold"),text_color="white").pack(anchor="w",padx=14,pady=(8,4))

        # ── Grid de botões responsivo (2 colunas) ──
        rgrid=ctk.CTkFrame(fac,fg_color="transparent"); rgrid.pack(fill="x",padx=14,pady=(4,10))
        rgrid.columnconfigure(0,weight=1,minsize=200)
        rgrid.columnconfigure(1,weight=1,minsize=200)

        # Linha 0 — Croqui
        ctk.CTkLabel(rgrid,text="Croqui",font=ctk.CTkFont(size=11),text_color="#aac0e0",
                     anchor="w").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,2))
        self.btn_kml=ctk.CTkButton(rgrid,text="🗺️ Gerar KML",command=self._kml,state="disabled",fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_kml.grid(row=1,column=0,sticky="ew",padx=(0,4),pady=(0,4))
        self.btn_shp=ctk.CTkButton(rgrid,text="📦 Gerar SHP",command=self._shp,state="disabled",fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_shp.grid(row=1,column=1,sticky="ew",padx=(4,0),pady=(0,4))

        # Linha 3 — Laudo
        ctk.CTkLabel(rgrid,text="Laudo",font=ctk.CTkFont(size=11),text_color="#aac0e0",
                     anchor="w").grid(row=3,column=0,columnspan=2,sticky="w",pady=(0,2))
        self.btn_capa=ctk.CTkButton(rgrid,text="📋 Preencher Capa",command=self._preencher_capa,
                                    state="disabled",fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_capa.grid(row=4,column=0,sticky="ew",padx=(0,4),pady=(0,4))
        self.btn_parecer=ctk.CTkButton(rgrid,text="📝 Preencher e salvar parecer",command=self._abrir_parecer,
                                       state="disabled",fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_parecer.grid(row=4,column=1,sticky="ew",padx=(4,0),pady=(0,8))

        # Linha 5 — Vistoria
        ctk.CTkLabel(rgrid,text="Vistoria",font=ctk.CTkFont(size=11),text_color="#aac0e0",
                     anchor="w").grid(row=5,column=0,columnspan=2,sticky="w",pady=(0,2))
        self.btn_validar=ctk.CTkButton(rgrid,text="✅ Marcar 'Não se aplica' e Corrigir nome das fotos",command=self._validar,
                                       state="disabled",fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_validar.grid(row=6,column=0,sticky="ew",padx=(0,4),pady=(0,4))
        self.btn_importar=ctk.CTkButton(rgrid,text="📥 Criar campos do laudo antigo e adicionar as fotos",command=self._importar,
                                        state="disabled",fg_color="#f5c518",hover_color="#d4a80e",text_color="#1a2c5b",text_color_disabled="#1a2c5b")
        self.btn_importar.grid(row=6,column=1,sticky="ew",padx=(4,0),pady=(0,4))

    def _log(self,msg):
        self._log_queue.put(msg)

    def _log_flush(self):
        """Drena a fila de log e atualiza o widget — chamado a 60Hz."""
        try:
            msgs=[]
            while not self._log_queue.empty():
                msgs.append(self._log_queue.get_nowait())
            if msgs:
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end","\n".join(msgs)+"\n")
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
        except Exception:
            pass
        self.after(16, self._log_flush)

    def _log_clear(self):
        self.txt_log.configure(state="normal"); self.txt_log.delete("1.0","end")
        self.txt_log.configure(state="disabled")

    def _setup_dragdrop(self):
        try:
            from tkinterdnd2 import DND_FILES
            # Registrar no drop_frame para feedback visual
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_frame.dnd_bind("<<DragEnter>>", lambda e: self.drop_frame.configure(fg_color="#c2d8f0"))
            self.drop_frame.dnd_bind("<<DragLeave>>", lambda e: self.drop_frame.configure(fg_color="#d6e4f5"))
            # Registrar em todos os frames filhos do canvas para cobrir toda a área
            for w in self._canvas.winfo_children():
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass
            self.lbl_pdf.configure(text="📂  Arraste o PDF aqui  ou  clique para selecionar")
        except Exception:
            pass

    def _on_drop(self, event):
        raw = event.data.strip()
        import re
        partes = re.findall(r'\{[^}]+\}|[^\s{}]+', raw)
        path = partes[0].strip("{}") if partes else raw.strip("{}")
        if path.lower().endswith(".pdf"):
            self.drop_frame.configure(fg_color="#d6e4f5")
            self._carregar_pdf(path)
        else:
            self.lbl_pdf.configure(text="⚠️ Apenas arquivos PDF", text_color="orange")

    def _conectar_auto(self):
        if not _ENV_CARREGADO:
            self._log("⚠️ Arquivo .env não encontrado. Crie um arquivo .env na pasta do programa com as credenciais.")
        if not JWT_PADRAO or not AUTH_BASIC:
            self._log("❌ Credenciais não configuradas. Verifique o arquivo .env.")
            return
        sistema=self.var_sistema.get()
        jwt = JWT_PADRAO_MAIS if sistema == "multiamais" else JWT_PADRAO
        self.jwt_token=jwt; self.api=MultiaAPI(jwt,sistema=sistema)
        nome_exib = "MultiA Mais" if sistema == "multiamais" else "MultiA"
        self.lbl_auth.configure(text=f"✅ Autenticado — {nome_exib}",text_color="#4ade80")
        self._log(f"✅ Autenticado no sistema '{nome_exib}'")
        self.btn_validar.configure(state="normal")

    def _sel_pdf(self):
        path=filedialog.askopenfilename(filetypes=[("PDF","*.pdf")])
        if path: self._carregar_pdf(path)

    def _carregar_pdf(self,path):
        self.pdf_path=path; nome=os.path.basename(path)
        self.lbl_pdf.configure(text=f"📄 {nome}",text_color="#1a2c5b")
        self.drop_frame.configure(fg_color="#d6e4f5",border_color="#f5c518")
        nums=re.findall(r"\d+",os.path.splitext(nome)[0])

    def _buscar(self):
        if not self.api: messagebox.showwarning("Auth","Conecte primeiro."); return
        busca=self.entry_mat.get().strip()
        if not busca: return
        def worker():
            try:
                self._log(f"🔍 Buscando avaliação REG '{busca}'...")
                avs=self.api.buscar_avaliacao(busca)
                if not avs: self._log("⚠️ Nenhuma avaliação encontrada."); return
                av=next((a for a in avs if str(a.get("REG",""))==str(busca)), avs[0])
                self.uuid_atual=av["UUID"]
                self._log(f"✅ Encontrada: REG {av['REG']} | {av.get('CIDADE','?')}/{av.get('UF','?')} | {av.get('STATUS','?')}")
                self._log(f"   UUID: {self.uuid_atual}")
                self.lbl_av.configure(text=f"REG {av['REG']} — {av.get('CIDADE','?')}",text_color="green")
                if self.dados_pdf: self.btn_capa.configure(state="normal")
            except Exception as ex: self._log(f"❌ Erro: {ex}")
        threading.Thread(target=worker,daemon=True).start()

    def _analisar(self):
        if not self.pdf_path: messagebox.showwarning("PDF","Selecione um PDF."); return
        tipo=self.var_tipo.get()
        _texto_original="🤖  Analisar PDF com Gemini"
        _animando={"ativo":True}
        def _animar():
            pontos=["",".","..",  "..."]
            i=0
            while _animando["ativo"]:
                self.btn_analisar.configure(text=f"🤖  Analisando{pontos[i%4]}")
                i+=1
                time.sleep(0.5)
            self.btn_analisar.configure(text=_texto_original,state="normal")
        self.btn_analisar.configure(state="disabled")
        threading.Thread(target=_animar,daemon=True).start()
        def worker():
            try:
                self._log_clear()
                self._log(f"📖 Lendo PDF: {os.path.basename(self.pdf_path)}")
                with open(self.pdf_path,"rb") as f: pdf_bytes=f.read()
                self._log(f"   Tamanho: {len(pdf_bytes)//1024} KB")
                self._log("🤖 Enviando para Gemini — aguardando resposta...")
                dados=chamar_gemini(pdf_bytes, PROMPT_NORMAL if tipo=="normal" else PROMPT_ANTIGO)
                self.dados_pdf=dados
                self._log("✅ Gemini respondeu!\n")
                self._log("─── DADOS EXTRAÍDOS ───")
                for c in ["cri","proprietarios","endereco","numero","bairro","complemento","cidade","uf",
                          "tipo_imovel","area_total","testada","profundidade","incra","car",
                          "rodovias_acesso","cidades_vizinhas","averbacoes"]:
                    v=dados.get(c)
                    self._log(f"  {'✔' if v else '—'} {c}: {v if v else '(não encontrado)'}")
                coord=dados.get("coordenadas")
                if coord and coord.get("pontos"):
                    fmt=coord.get("formato","?"); npts=len(coord["pontos"])
                    self._log(f"\n📐 Coordenadas: {npts} pontos | Formato: {fmt}"
                              +(f" | Zona {coord.get('zona_utm','')} {coord.get('hemisferio','')}" if fmt=="UTM" else ""))
                    self.btn_kml.configure(state="normal")
                    if fmt=="UTM": self.btn_shp.configure(state="normal")
                else:
                    self._log("\n⚠️ Coordenadas não encontradas.")
                if tipo=="antigo":
                    grupos=dados.get("grupos_vistoria",[])
                    self._log(f"\n📦 Grupos de vistoria: {len(grupos)}")
                    for g in grupos:
                        self._log(f"   {'📷' if g.get('tem_foto') else '📋'} {g.get('nome','?')} | Área: {g.get('area') or '—'} | Constr: {g.get('construcao','N')}")
                    self.btn_importar.configure(state="normal")
                self.btn_parecer.configure(state="normal")
                self.btn_capa.configure(state="normal" if self.api and self.uuid_atual else "disabled")
                self.btn_validar.configure(state="normal" if self.api else "disabled")
                if tipo=="antigo":
                    self.btn_importar.configure(state="normal" if self.api else "disabled")
                self._log("\n✅ Análise concluída! Use os botões abaixo.")
                if not self.api:
                    self._log("   ⚠️ Conecte ao sistema para usar as ações da API.")
                if not self.uuid_atual:
                    self._log("   ⚠️ Busque a matrícula no sistema para salvar os dados.")
            except (SSLError, ReqConnError) as ex:
                import traceback
                self._log(f"❌ Erro de conexão/SSL: {ex}")
                self._log("   💡 Verifique sua conexão ou tente novamente em alguns instantes.")
                self._log(traceback.format_exc())
            except Timeout as ex:
                import traceback
                self._log(f"❌ Timeout na comunicação com Gemini: {ex}")
                self._log("   💡 O PDF pode ser muito grande ou a rede está lenta. Tente novamente.")
                self._log(traceback.format_exc())
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())
            finally:
                _animando["ativo"]=False
        threading.Thread(target=worker,daemon=True).start()

    def _kml(self):
        if not self.dados_pdf: return
        coord=self.dados_pdf.get("coordenadas")
        if not coord: messagebox.showwarning("KML","Sem coordenadas."); return
        pasta=filedialog.askdirectory(title="Pasta para KML")
        if not pasta: return
        def worker():
            try:
                self._log("🗺️ Gerando KML...")
                pontos=normalizar_pontos(coord)
                self._log(f"   {len(pontos)} pontos convertidos para WGS84")
                nome=self._nome(); data=gerar_kml_bytes(pontos,nome)
                path=os.path.join(pasta,nome+".kml")
                with open(path,"wb") as f: f.write(data)
                self._log(f"✅ KML salvo: {path}")
            except Exception as ex: self._log(f"❌ Erro KML: {ex}")
        threading.Thread(target=worker,daemon=True).start()

    def _shp(self):
        if not self.dados_pdf: return
        coord=self.dados_pdf.get("coordenadas")
        if not coord or coord.get("formato","").upper()!="UTM":
            messagebox.showwarning("SHP","SHP apenas para UTM."); return
        pasta=filedialog.askdirectory(title="Pasta para SHP")
        if not pasta: return
        def worker():
            try:
                self._log("📦 Gerando SHP...")
                zona=int(coord.get("zona_utm") or 22)
                path=gerar_shp(coord["pontos"],zona,self._nome(),pasta)
                self._log(f"✅ SHP salvo: {path}")
            except Exception as ex: self._log(f"❌ Erro SHP: {ex}")
        threading.Thread(target=worker,daemon=True).start()

    def _croqui_api(self):
        if not self.api or not self.uuid_atual or not self.dados_pdf:
            messagebox.showwarning("API","Conecte, busque e analise o PDF primeiro."); return
        coord=self.dados_pdf.get("coordenadas")
        if not coord: messagebox.showwarning("Coord","Sem coordenadas."); return
        def worker():
            try:
                self._log("☁️ Preparando croqui para envio...")
                pontos=normalizar_pontos(coord)
                self._log(f"   {len(pontos)} pontos convertidos para WGS84")
                coords_str="; ".join([f"{lat:.6f}, {lon:.6f}" for lat,lon in pontos])
                self._log("   Salvando dados do croqui na API...")
                self.api.salvar_dados_croqui(self.uuid_atual,coords_str)
                self._log("   ✔ Dados salvos")
                self._log("   Gerando arquivo KML...")
                kml_bytes=gerar_kml_bytes(pontos,self._nome())
                self._log(f"   KML: {len(kml_bytes)} bytes — enviando...")
                self.api.salvar_imagem_croqui(self.uuid_atual,kml_bytes,self._nome()+".kml")
                self._log("✅ Croqui enviado com sucesso!")
            except Exception as ex: self._log(f"❌ Erro croqui: {ex}")
        threading.Thread(target=worker,daemon=True).start()

    def _preencher_capa(self):
        if not self.api or not self.uuid_atual:
            messagebox.showwarning("API","Conecte e busque a avaliação primeiro."); return
        if not self.dados_pdf:
            messagebox.showwarning("PDF","Analise o PDF primeiro."); return
        def worker():
            try:
                self._log("\n📋 Preenchendo campos da capa...")
                self._log("   Buscando campos da avaliação na API...")
                self.api.salvar_campos_capa(self.uuid_atual, self.dados_pdf, log_fn=self._log, tipo_laudo=self.var_tipo.get())
                # Laudo antigo: envia parecer junto com a capa
                if self.var_tipo.get()=="antigo" and self.dados_pdf.get("parecer"):
                    self._log("\n📝 Laudo antigo — salvando parecer junto...")
                    parecer=self.dados_pdf["parecer"].strip()
                    paragrafos=[p.strip() for p in re.split(r"\n{2,}",parecer) if p.strip()]
                    parecer="\n\n".join(paragrafos)
                    self.api.salvar_parecer(self.uuid_atual,parecer,log_fn=self._log)
                    self._log("✅ Parecer salvo!")
                self._log("✅ Capa preenchida com sucesso!")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro ao preencher capa: {ex}")
                self._log(traceback.format_exc())
        threading.Thread(target=worker,daemon=True).start()


    def _abrir_parecer(self):
        if not self.dados_pdf: messagebox.showwarning("PDF","Analise o PDF primeiro."); return
        JanelaParecer(self,self.dados_pdf,self._salvar_parecer)

    def _salvar_parecer(self,opcoes):
        def worker():
            try:
                self._log("\n📝 Gerando parecer...")
                tipo=self.var_tipo.get()
                if tipo=="antigo" and self.dados_pdf.get("parecer"):
                    parecer=self.dados_pdf["parecer"].strip()
                    self._log("   Usando parecer extraído do laudo antigo")
                    # Garante 4 parágrafos separados por linha em branco
                    paragrafos=[p.strip() for p in re.split(r"\n{2,}",parecer) if p.strip()]
                    parecer="\n\n".join(paragrafos)
                    self._log(f"   {len(paragrafos)} parágrafo(s) identificado(s)")
                else:
                    parecer=gerar_parecer(self.dados_pdf,opcoes)
                    self._log("   Parecer gerado com as opções selecionadas")
                self._log(f"   Tamanho: {len(parecer)} caracteres")
                self._log("\n─── PRÉVIA ───")
                for l in parecer.split("\n")[:6]: self._log("  "+l)
                self._log("  [...]")
                if not self.api:
                    self._log("\n❌ Não conectado — conecte ao sistema primeiro."); return
                if not self.uuid_atual:
                    self._log("\n❌ Nenhuma avaliação buscada — busque o código Infoel primeiro."); return
                self._log("\n☁️ Salvando parecer no sistema...")
                self.api.salvar_parecer(self.uuid_atual,parecer,log_fn=self._log)
                self._log("✅ Parecer salvo com sucesso!")

                # Criar grupos de divisões de área na vistoria
                divisoes = opcoes.get("divisoes_area", {})
                tipo_imovel = (self.dados_pdf or {}).get("tipo_imovel","URBANO").upper()
                unid_padrao = "ha" if tipo_imovel == "RURAL" else "m²"
                area_total_raw = (self.dados_pdf or {}).get("area_total","")

                def _to_float(v):
                    import re as _re
                    if not v: return 0.0
                    n = _re.sub(r"[^\d,.]","",str(v)).strip()
                    if _re.match(r"^\d{1,3}(\.\d{3})+,\d+$",n): n=n.replace(".","").replace(",",".")
                    else: n=n.replace(",",".")
                    try: return float(n)
                    except: return 0.0

                def _criar_grupo_area(nome, area_str, unidade):
                    self._log(f"   ➕ Criando grupo '{nome}' — {area_str} {unidade}...")
                    self.api.adicionar_grupo(self.uuid_atual, nome)
                    vs = self.api.buscar_vistoria(self.uuid_atual)
                    gs = vs.get('grupos') or vs.get('gruposImoveis') or []
                    if not gs: self._log("   ⚠️ Grupo não retornado"); return
                    reg = max(g['REG'] for g in gs)
                    area_api = area_str.replace(",",".")
                    self.api.editar_grupo(self.uuid_atual, reg, nome=nome,
                                         area=area_api, unidade=unidade,
                                         nao_se_aplica=False)
                    self._log(f"   ✔ REG={reg} salvo")

                if divisoes:
                    self._log("\n🌿 Criando grupos de divisões de área...")
                    area_total_f = _to_float(area_total_raw)
                    nomes = list(divisoes.keys())

                    if len(nomes) == 1:
                        # Só uma divisão: usa área total
                        area_str = normalizar_area(area_total_raw) or "0"
                        _criar_grupo_area(nomes[0], area_str, unid_padrao)
                    else:
                        # Múltiplas: calcular o que ficou vazio (já foi calculado na UI, mas recalcula por segurança)
                        soma = sum(_to_float(v) for v in divisoes.values() if v)
                        vazios = [n for n,v in divisoes.items() if not v]
                        if len(vazios) == 1 and area_total_f > 0:
                            divisoes[vazios[0]] = f"{(area_total_f - soma):.4f}".replace(".",",")
                        for nome, val in divisoes.items():
                            area_str = normalizar_area(val) if val else normalizar_area(area_total_raw) or "0"
                            _criar_grupo_area(nome, area_str, unid_padrao)
                else:
                    self._log("\n⚠️ Nenhuma divisão de área selecionada — nenhum grupo criado")
                coord_manual = opcoes.get("coordenadas_manual","").strip()
                if coord_manual:
                    self._log(f"\n📍 Salvando coordenadas: {coord_manual}")
                    self.api.salvar_campo(self.uuid_atual, 53, coord_manual)
                    self._log(f"   ✔ Coordenadas salvas")

                # Salvar fonte do croqui na capa (REG fixo = 60)
                fonte = opcoes.get("fonte_croqui","").strip()
                if fonte:
                    self._log(f"\n🗺️ Salvando fonte do croqui...")
                    self.api.salvar_campo(self.uuid_atual, 60, fonte)
                    self._log(f"   ✔ Fonte do croqui salva")
            except Exception as ex:
                import traceback; self._log(f"❌ Erro parecer: {ex}"); self._log(traceback.format_exc())
        threading.Thread(target=worker,daemon=True).start()

    def _validar(self):
        if not self.api or not self.uuid_atual:
            messagebox.showwarning("API","Conecte e busque a avaliação."); return
        if not messagebox.askyesno("Confirmar",
            "Marcará TODOS os campos da aba 'Validar Vistoria' como 'Não se aplica'.\n"
            "Campos com valor serão registrados no log.\n\nContinuar?"): return
        def worker():
            try:
                self._log("\n✅ Iniciando validação da vistoria...")
                self._log("   Buscando campos na API...")
                ja=self.api.validar_vistoria_nao_aplica(self.uuid_atual,log_fn=self._log)
                self._log("─"*40)
                if ja:
                    self._log(f"⚠️  {len(ja)} grupo(s) tinham valor preenchido antes de marcar 'Não se aplica':\n")
                    linhas_txt=[]
                    import datetime, os
                    linhas_txt.append(f"Validação — {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
                    linhas_txt.append(f"UUID: {self.uuid_atual}")
                    linhas_txt.append("─"*50)
                    for item in ja:
                        self._log(f"  📌 {item['nome']}")
                        linhas_txt.append(f"\n{item['nome']}")
                        if item.get('area') and item['area'] not in ("","0","0.00"):
                            self._log(f"     • Área: {item['area']} m²")
                            linhas_txt.append(f"  Área: {item['area']} m²")
                        if item.get('valor') and item['valor'] not in ("","0","0.00"):
                            self._log(f"     • Valor unitário: {item['valor']}")
                            linhas_txt.append(f"  Valor unitário: {item['valor']}")
                        if item.get('obs') and item['obs'].lower() not in ("","não se aplica","nao se aplica"):
                            self._log(f"     • Observação: {item['obs']}")
                            linhas_txt.append(f"  Observação: {item['obs']}")
                    # Salvar .txt na mesma pasta do PDF ou pasta do usuário
                    pasta = os.path.dirname(self.pdf_path) if self.pdf_path else os.path.expanduser("~")
                    nome_arq = f"vistoria_{self.uuid_atual[:8]}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    caminho = os.path.join(pasta, nome_arq)
                    with open(caminho, "w", encoding="utf-8") as f:
                        f.write("\n".join(linhas_txt))
                    self._log(f"\n💾 Dados salvos em: {caminho}")
                else:
                    self._log("ℹ️  Nenhum grupo tinha valor preenchido.")
                self._log("\n✅ Todos marcados como 'Não se aplica'!")
            except Exception as ex:
                import traceback; self._log(f"❌ Erro: {ex}"); self._log(traceback.format_exc())
        threading.Thread(target=worker,daemon=True).start()

    def _importar(self):
        if not self.api or not self.uuid_atual:
            messagebox.showwarning("API","Conecte e busque a avaliação."); return
        if not self.dados_pdf or not self.pdf_path:
            messagebox.showwarning("PDF","Analise o PDF primeiro."); return
        grupos=self.dados_pdf.get("grupos_vistoria",[])
        self._log(f"\n🔍 Debug grupos: tipo={type(grupos).__name__} | valor={grupos}")
        if not grupos:
            messagebox.showwarning("Grupos","Nenhum grupo encontrado no PDF.\nTente analisar o PDF novamente."); return

        # ── Fase 1: extrair fotos e confirmar ──
        self._log("\n🔍 Extraindo fotos para pré-visualização...")
        hdrs={k:v for k,v in self.api.headers.items()}
        sistema=self.var_sistema.get()
        dl_session=obter_sessao_autenticada(sistema=sistema,log_fn=self._log)
        fotos=extrair_fotos_qrcode_pdf(self.pdf_path,jwt_headers=hdrs,log_fn=self._log,sistema=sistema,download_session=dl_session)
        if not fotos:
            self._log("   ⚠️ Nenhum QR code encontrado, tentando extração direta...")
            fotos=extrair_fotos_pdf(self.pdf_path)

        n_fotos=len(fotos)
        self._log(f"   📷 {n_fotos} foto(s) encontrada(s)")
        for f_ in fotos: self._log(f"     📷 '{f_.get('nome','?')}'")

        grupos_area=[g for g in grupos
                     if normalizar_area(str(g.get('area') or '').strip())
                     not in ('','0','0,00','null','None')]

        msg=(f"Grupos de vistoria: {len(grupos_area)} grupo(s) com área\n"
             f"Fotos encontradas: {n_fotos} foto(s)\n\n"
             f"Quer criar os grupos e adicionar as fotos?")
        if not messagebox.askyesno("Confirmar importação", msg): return

        # ── Fase 2: importar em thread ──
        def worker():
            try:
                self._log(f"\n📥 Iniciando importação...")
                self._log(f"   Grupos recebidos: {len(grupos)}")
                for g in grupos: self._log(f"     • {g}")

                def _criar_grupo_e_reg(nome):
                    self._log(f"   ➕ Criando grupo '{nome}'...")
                    self.api.adicionar_grupo(self.uuid_atual,nome)
                    vs=self.api.buscar_vistoria(self.uuid_atual)
                    gs=(vs.get('grupos') or vs.get('gruposImoveis') or [])
                    if not gs:
                        self._log("   ⚠️ Nenhum grupo retornado pela API")
                        return None
                    reg=max(g['REG'] for g in gs)
                    self._log(f"   ✔ REG={reg}")
                    return reg

                # Grupos de área — sem foto
                self._log(f"\n   Grupos com área: {len(grupos_area)}")
                for i,grupo in enumerate(grupos_area):
                    nome=_title_grupo(grupo.get('nome',f'Grupo {i+1}'))
                    area_val=normalizar_area(str(grupo.get('area') or '').strip())
                    unidade=str(grupo.get('unidade_area') or 'm²').strip()
                    self._log(f"\n  [área {i+1}/{len(grupos_area)}] '{nome}' — {area_val} {unidade}")
                    reg=_criar_grupo_e_reg(nome)
                    if not reg: self._log('   ⚠️ REG não encontrado — pulando'); continue
                    self.api.editar_grupo(self.uuid_atual,reg,
                        nome=nome,
                        construcao=grupo.get('construcao','N'),
                        averbado=grupo.get('averbado','N'),
                        area=area_val,
                        unidade=unidade,
                        valor_unidade=str(grupo.get('valor_unidade') or ''),
                        obs=str(grupo.get('obs') or ''),
                        nao_se_aplica=False)
                    self._log(f"   ✔ Área salva: {area_val} {unidade}")

                # Grupo FOTOS
                if fotos:
                    self._log(f"\n  [fotos] Criando grupo 'Fotos' com {n_fotos} foto(s)...")
                    reg_fotos=_criar_grupo_e_reg('Fotos')
                    if reg_fotos:
                        self.api.editar_grupo(self.uuid_atual,reg_fotos,
                            nome='Fotos',construcao='N',averbado='N',
                            area='0',valor_unidade='',obs='',nao_se_aplica=True)
                        fotos_enviadas=0
                        for j,foto in enumerate(fotos):
                            nome_foto=foto.get('nome') or f'Foto {j+1}'
                            fn=f"foto_{j+1}.{foto['ext']}"
                            self._log(f"   📷 [{j+1}/{n_fotos}] '{nome_foto}'")
                            try:
                                resp_img=self.api.salvar_imagem_grupo(self.uuid_atual,reg_fotos,foto['bytes'],fn)
                                fotos_enviadas+=1
                                reg_img=(resp_img.get('dados',{}).get('REG') or
                                         resp_img.get('REG') or
                                         resp_img.get('dados',{}).get('reg'))
                                if not reg_img:
                                    vs2=self.api.buscar_vistoria(self.uuid_atual)
                                    imgs=[img for img in (vs2.get('imagens') or [])
                                          if str(img.get('REGGRUPO') or img.get('regGrupo',''))==str(reg_fotos)]
                                    if imgs: reg_img=max(img.get('REG') or img.get('reg') for img in imgs)
                                if reg_img:
                                    self.api.editar_descricao_imagem(self.uuid_atual,reg_fotos,reg_img,nome_foto)
                                    self._log(f"     ✔ '{nome_foto}'")
                            except Exception as ex_f:
                                self._log(f"   ⚠️ Erro foto {j+1}: {ex_f}")
                        self._log(f"   ✅ {fotos_enviadas}/{n_fotos} foto(s) enviada(s)")
                    else:
                        self._log('   ⚠️ Não foi possível criar grupo Fotos')
                else:
                    self._log('\n  ⚠️ Nenhuma foto encontrada no PDF')

                self._log("\n"+"─"*40)
                self._log(f"✅ Importação concluída! Grupos área: {len(grupos_area)} | Fotos: {n_fotos}")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())
        threading.Thread(target=worker,daemon=True).start()

    def _nome(self):
        d=self.dados_pdf or {}
        raw=f"{d.get('endereco','croqui')}_{d.get('numero','')}".strip("_")
        return re.sub(r"[^\w\-]","_",raw)[:60] or "croqui"

# ──────────────────────────────────────────────────────────────
