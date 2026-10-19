# Consulta à Minuta do Plano Diretor (Rev.05)

Ferramenta estática para a Comissão da Câmara Municipal de Curitiba. Não precisa de servidor: funciona no GitHub Pages, na Vercel ou abrindo o `index.html` direto do computador.

## Arquivos

| Caminho | O que é |
|---|---|
| `index.html` | Consulta: busca literal, índice, glossário e a aba "Pergunte à minuta" |
| `grafo.html` | Grafo de impactos (rede radial com remissões) |
| `assets/estilo.css` | Visual da Minuta Interativa (claro e escuro) |
| `assets/busca.js` | Motor de busca e leitor de artigos, usado pelas duas páginas |
| `dados/minuta_rev05.js` | Texto estruturado da minuta (carregado pelas páginas) |
| `dados/minuta_rev05.json` | Mesmo conteúdo em JSON puro |
| `dados/minuta_rev05_chunks.jsonl` | Um chunk por artigo, com metadados, pronto para a ingestão no Qdrant |
| `scripts/parse_minuta.py` | Gera os três arquivos de `dados/` a partir do .docx |

## Atualizar quando sair nova revisão

```bash
pip install python-docx
python3 scripts/parse_minuta.py "nova_minuta.docx" dados
```

O script reconstrói a numeração automática do Word (artigos a partir do 10, incisos em romano, alíneas) e imprime avisos se a sequência digitada divergir da automática.

## Ligar a aba "Pergunte à minuta" (n8n)

A ingestão no Qdrant é feita pelo notebook `IPPUC_INGESTAO_MINUTA_OUTUBRO_2026.ipynb` (coleções `minuta_outubro_2026` e `glossario_outubro_2026`, com os aliases `minuta_ultima_versao` e `glossario_ultima_versao`).

O workflow `n8n/minuta_n8n_perguntar.json` (importe em *Import from File*) responde às perguntas:

1. Lê pelo alias os 355 artigos e o glossário, a cada pergunta.
2. Varre todos os artigos por programa: artigos citados pelo número, termos entre aspas, remissões, mesma seção e conceitos compartilhados (siglas, nomes próprios, termos do glossário).
3. Manda ao Claude Haiku 5.5 só os artigos que a varredura achou (modo `economico`, abaixo de 100 mil tokens, menos de 1 centavo de dólar por pergunta) e a lista da varredura, que ele é obrigado a avaliar artigo por artigo. Com `MODO = 'completo'` no node Configurar e Validar, manda a minuta inteira (mais caro).
4. Devolve a resposta, a avaliação de cada artigo (impacto direto, indireto ou sem impacto) e a varredura. O site aponta os artigos da lista que a IA não comentou.

Credenciais: Qdrant (Header Auth `api-key`) nos dois nodes de leitura; Anthropic (Header Auth `x-api-key`) no node Claude.

O `index.html` já aponta `ASK_URL` para `https://n8n.planodiretorcuritiba.cloud/webhook/minuta-perguntar`. Se `ASK_URL` ficar vazio, a aba mostra um aviso e manda o usuário para a busca literal.

## Limites conhecidos

- As remissões são detectadas por padrão de texto ("art. 45", "Título VI desta Lei", "Seção III deste Capítulo", "Anexo 3"). A Rev.05 quase não remete a artigos por número; por isso o grafo também liga entre si os artigos que contêm o termo buscado.
- Os tipos de comando (obrigação, permissão etc.) são classificados por expressões ("deverá", "poderá", "é vedado"...). Servem para triagem, não substituem a leitura.
