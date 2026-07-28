# MultiA — Analisador de Matrículas com IA

Sistema desktop (Python + PyWebView) que lê PDFs de matrícula de imóveis, extrai os dados
automaticamente com IA (Gemini) e monta o parecer de avaliação, incluindo cálculo de áreas,
geração de KML/shapefile a partir das coordenadas, extração de fotos do croqui e integração
com a plataforma Infoel.

## Estrutura do repositório

- **`multia/`** + `main_webview.py` + `MultiA PDF Analyzer.spec` — aplicativo principal (MPA),
  com todas as funcionalidades: análise de PDF, parecer completo, fotos, planilha, CAR.
- **`frontend/`** — interface (HTML/CSS/JS) usada pelo MPA.

> Existe uma versão reduzida do sistema, o "MultiA Central" (focada em mesclagem de múltiplas
> matrículas), mantida como projeto separado.

## Pré-requisitos

- Python 3.10 ou superior
- Uma chave de API do [Google Gemini](https://ai.google.dev/)
- Um arquivo de credenciais de conta de serviço do Google (para integração com planilhas)
- Acesso (login) à API da Infoel

## Configuração

1. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

2. Copie `.env.example` para `.env` e preencha com suas credenciais reais:

   ```bash
   cp .env.example .env
   ```

3. Coloque o arquivo `credentials.json` (conta de serviço do Google) na raiz do projeto,
   ao lado de `main_webview.py`. Esse arquivo **nunca** deve ser commitado — ele já está
   listado no `.gitignore`.

## Executando

```bash
python main_webview.py
```

## Gerando o executável (.exe)

O empacotamento usa [PyInstaller](https://pyinstaller.org/) através dos arquivos `.spec`
já presentes no repositório:

```bash
pyinstaller "MultiA PDF Analyzer.spec"
```

O executável é gerado em `dist/` (pasta ignorada pelo Git).

## Segurança

- Nenhuma credencial deve ser commitada. `.env`, `credentials.json` e `multia_config.json`
  já estão no `.gitignore`.
- Se qualquer uma dessas credenciais já foi exposta anteriormente (histórico local, cópias
  soltas, etc.), gere novas antes de tornar este repositório público.

## Autor

Leandro José Busarello
