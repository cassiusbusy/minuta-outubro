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

## Ligar a aba "Pergunte à minuta"

Em `index.html`, preencha `ASK_URL` com o webhook do n8n. Contrato:

```
POST { "pergunta": "...", "versao": "rev05" }
→   { "resposta": "...", "fontes": [ { "artigo": 123 }, ... ] }
```

Enquanto `ASK_URL` estiver vazio, a aba mostra um aviso e manda o usuário para a busca literal.

## Limites conhecidos

- As remissões são detectadas por padrão de texto ("art. 45", "Título VI desta Lei", "Seção III deste Capítulo", "Anexo 3"). A Rev.05 quase não remete a artigos por número; por isso o grafo também liga entre si os artigos que contêm o termo buscado.
- Os tipos de comando (obrigação, permissão etc.) são classificados por expressões ("deverá", "poderá", "é vedado"...). Servem para triagem, não substituem a leitura.
