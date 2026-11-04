# Consulta à Minuta do Plano Diretor (Rev.05)

Ferramenta estática para a Comissão da Câmara Municipal de Curitiba. Não precisa de servidor: funciona no GitHub Pages, na Vercel ou abrindo o `index.html` direto do computador.

## Arquivos

| Caminho | O que é |
|---|---|
| `index.html` | Consulta: busca literal, índice, glossário e a aba "Pergunte à minuta" |
| `grafo.html` | Grafo de impactos (rede radial com remissões) |
| `artigos.html` | **Escondida nesta fase** (fora do menu; para voltar, ver comentários em `index.html`, `grafo.html`, `comparativo.html` — `MOSTRAR_ARTIGOS` — e `OCULTA` em `versoes.html`/`lei2015.html`). Artigo por artigo: os 355 artigos da Rev.05 r1 com as duas comparações juntas — em relação à Lei nº 14.771/2015 (igual, parcialmente igual, novo, em oposição; tipo de mudança e impacto) e desde a versão da consulta pública de julho/2026 (alterado com intensidade, novo, ajuste de forma, sem alteração; diferenças palavra a palavra). Filtros combináveis, cruzamento das duas classificações, atalhos (ex.: em oposição e mudaram depois da consulta), justificativas técnicas e a lista dos artigos de 2015 sem correspondente |
| `versoes.html`, `lei2015.html` | Antigas páginas "O que mudou" e "Lei de 2015 × nova": redirecionam para `artigos.html` levando os filtros do link; enquanto ela está escondida, levam ao `comparativo.html` |
| `dados/lei2015_x_rev05.js` | Dados da comparação com a lei de 2015 |
| `comparativo.html` | Comparativo visual: bolinhas dos artigos de 2015 e da nova minuta ligadas pela classificação; ao clicar, abre o artigo, as contribuições da população (numeradas) e a relação com as leis federais; pode pintar pela mudança desde a consulta pública; busca por palavra, expressão entre aspas ou número (acende os artigos no diagrama, lista os resultados com o trecho destacado e pode incluir contribuições e justificativas; fica no link como `?q=`) |
| `dados/comparativo_vis.js`, `dados/contribuicoes_vis.js` | Dados dessa página (vínculos com leis federais; contribuições e seus vínculos com os artigos) |
| `dados/justificativas.js` | Justificativas técnicas: `JUSTIFICATIVAS` por artigo da nova minuta e `JUSTIFICATIVAS_2015` por artigo da lei de 2015 sem correspondente (editável à mão: copie um bloco, troque o número do artigo e preencha os campos). Artigos que estão aqui ganham o anel no diagrama, o bloco "Justificativa técnica" no painel e entram na lista do botão "Justificativas técnicas" |
| `dados/versoes_jul_out.js` | Dados da comparação julho × outubro (pareamento por texto e diferenças palavra a palavra) |
| `dados/consulta_vis.js` | Resumo da comparação julho × outubro por artigo, para pintar o comparativo visual (gerado de `versoes_jul_out.js`) |
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

## Aba "Pergunte usando I.A." (n8n)

Workflow: `n8n/minuta_n8n_orquestrador.json` (endereço `/webhook/minuta-perguntar-orq`, ativo junto com os outros dois; a página `comparar.html` testa os três lado a lado). Ele classifica a pergunta e só chama a IA quando precisa:

| Tipo | Exemplo | Como responde | Custo |
|---|---|---|---|
| localizar/contar | quais artigos têm "poderá"? | lista exata por programa | zero |
| ler artigo | o que diz o art. 62? | texto integral | zero |
| definição | o que é DOTS? | glossário + onde o termo aparece | zero |
| alteração/impacto | trocar "poderá" por "deverá" no art. 63 | varredura + Claude Haiku | ~US$ 0,002–0,006 |
| tema | o que a minuta prevê sobre calçadas? | varredura por palavras + Claude Haiku | ~US$ 0,002–0,004 |

Pergunta igual a uma já respondida pela IA (mesma versão da minuta) volta do cache, sem custo.

Banco: rode `n8n/minuta_banco.sql` no Supabase do MultipleBrains (cria só `minuta_consulta`, `minuta_avaliacao`, duas funções e a view `vw_minuta_custo_diario`). Credenciais: Postgres (a do MultipleBrains), Qdrant e Anthropic. Versão com ChatGPT: `n8n/minuta_n8n_orquestrador_openai.json` (mesmo endereço, modelo gpt-6-luna, credencial OpenAI); deixe ativa só uma das duas. Se o banco estiver fora do ar, o fluxo responde normalmente, só sem cache e sem registro.

Quando ingerir uma minuta nova, troque `VERSAO` no node Configurar e Validar: o cache é separado por versão.

## Limites conhecidos

- As remissões são detectadas por padrão de texto ("art. 45", "Título VI desta Lei", "Seção III deste Capítulo", "Anexo 3"). A Rev.05 quase não remete a artigos por número; por isso o grafo também liga entre si os artigos que contêm o termo buscado.
- Os tipos de comando (obrigação, permissão etc.) são classificados por expressões ("deverá", "poderá", "é vedado"...). Servem para triagem, não substituem a leitura.
