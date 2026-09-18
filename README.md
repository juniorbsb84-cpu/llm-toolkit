# llm-toolkit

Uma pergunta, qualquer provedor, de qualquer projeto — e, quando a pergunta é
difícil, vários modelos ao mesmo tempo.

Seis scripts Python sem dependência externa (só a biblioteca padrão),
construídos um em cima do outro:

```
llm.py            uma pergunta -> um provedor
  esquadrao.py      a mesma pergunta -> N provedores em paralelo
  equipe.py         um plano com dependências -> cada tarefa no seu provedor
  modelos.py        varre que modelos cada provedor oferece hoje
  painel.py         escolhe e dispara o lote pelo navegador
  inicia_painel.py  sobe o painel sem console, ou só abre o navegador
```

## Por que existe

Cada provedor de LLM tem seu próprio CLI, seu próprio formato de erro e sua
própria maneira de falhar em silêncio. Este toolkit põe todos atrás de uma
interface só e — a parte que importa — **trata a falha disfarçada como falha**.

O caso que motivou boa parte do código: com a sessão do `claude` expirada, o
CLI imprime `Failed to authenticate: OAuth session expired` no stdout **com
código de saída zero**. Sem uma lista explícita de "isto não é uma resposta",
essa linha vira a opinião do modelo, entra no dossiê da rodada seguinte, os
outros modelos criticam uma posição que nunca existiu, e o juiz consolida em
cima disso. Uma falha declarada custa um provedor; uma falha disfarçada
contamina a rodada inteira.

## Instalação

Requer Python 3.8+. Não há `pip install`: clone e rode.

```bash
git clone https://github.com/<usuario>/llm-toolkit
cd llm-toolkit
python tools/llm.py codex "teste"
```

### Chaves

Os provedores por HTTP (`openrouter`, `alibaba`, `deepseek`) leem a chave de um
arquivo de texto **fora do repositório**:

```bash
cp exemplos/llm-apis.txt.example ~/.llm-apis.txt
# edite com suas chaves reais
```

Outro caminho? Aponte a variável `LLM_APIS`. A chave nunca é impressa, nem em
mensagem de erro. O `.gitignore` bloqueia `.llm-apis.txt`, `APIs.txt`, `.env` e
`*.key` — mas o arquivo certo mora **fora** da árvore do repositório.

Os provedores por CLI (`agy`, `codex`, `claude`, `opencode`, `commandcode`)
usam a sessão já logada na máquina — não precisam de chave nenhuma aqui.

## Uso

### Uma pergunta

```bash
python tools/llm.py codex "por que este teste falha?"
python tools/llm.py openrouter -i captura.png "o que há de errado nesta tela?"
echo "texto longo" | python tools/llm.py agy
```

### Vários modelos ao mesmo tempo

```bash
python tools/esquadrao.py "como estruturar este módulo?"
python tools/esquadrao.py --modo refino "vale a pena migrar para X?"
cat contrato.txt | python tools/esquadrao.py --so agy,codex,openrouter
```

Quatro modos:

| Modo | O que faz |
|---|---|
| `todos` | cada provedor responde sozinho; você lê as N respostas |
| `refino` | N rodadas: independente, crítica cruzada, consolidação |
| `arquiteto` | o juiz quebra o objetivo em 2–6 subtarefas e roteia por especialidade |
| `consenso` | laço até todos ratificarem a mesma síntese, não um número fixo de rodadas |

**O valor não está em ter N respostas, está na divergência entre elas.** Onde
todos concordam não precisa de revisão humana; onde racha é onde o problema é
difícil de verdade. No modo `consenso`, se o teto de rodadas estourar, o que
sobrou em aberto *é* o resultado — quer dizer que o ponto é disputado, não que
a execução falhou.

### Um plano com dependências

`equipe.py` é só o motor: não decide nada, respeita a ordem e roda em paralelo
o que não depende de mais nada.

```json
[
  {"id": "levanta", "provedor": "agy",        "tarefa": "Levante os fatos sobre X"},
  {"id": "critica", "provedor": "codex",      "tarefa": "Ache os erros",
   "depende": ["levanta"]},
  {"id": "redige",  "provedor": "openrouter", "tarefa": "Escreva a versão final",
   "depende": ["levanta", "critica"]}
]
```

```bash
python tools/equipe.py plano.json
```

A saída de cada dependência entra no prompt da seguinte, rotulada pelo id. Uma
tarefa que falha não derruba as independentes; quem dependia dela é marcada
`PULADA` — rodar sem a entrada produziria resposta inventada, que é pior que
não produzir nada.

### Painel

```bash
python tools/inicia_painel.py   # sobe sem console e abre http://127.0.0.1:8777
python tools/painel.py          # sobe no terminal atual
```

Escolhe provedor, modelo e effort, dispara o lote e acompanha o progresso ao
vivo (Server-Sent Events na rota `/eventos`). A configuração é gravada em
`~/.claude/esquadrao.json`, que `esquadrao.py` e `equipe.py` leem depois.

O servidor escuta **só em `127.0.0.1`** e as rotas são uma lista fechada — não
há caminho vindo do request virando caminho de arquivo, nem `shell=True`, nem
`eval`. Ainda assim: ele não tem autenticação, porque assume que quem está na
máquina é você. Não exponha a porta 8777 para fora do host.

### Catálogo de modelos

```bash
python tools/modelos.py            # varre os provedores e atualiza o cache
python tools/modelos.py --mostra   # só lê o cache
```

Lista de modelos escrita à mão envelhece calada: o wrapper continua mandando um
nome que o provedor já aposentou e o erro só aparece na hora da pergunta. A
varredura mora aqui e o painel só lê o cache.

## Configuração

| Arquivo | Escrito por | Lido por |
|---|---|---|
| `~/.llm-apis.txt` (ou `$LLM_APIS`) | você | `llm.py` |
| `~/.claude/esquadrao.json` | `painel.py` | `esquadrao.py`, `equipe.py` |
| `~/.claude/perfis.json` | você, à mão | `esquadrao.py` (modo arquiteto) |
| `~/.claude/modelos.json` | `modelos.py` | `painel.py` |

Exemplos comentados em [`exemplos/`](exemplos/). Nenhum é obrigatório: sem eles
valem os padrões embutidos no código.

Precedência de modelo e effort: **o plano manda, o painel sugere, o código tem
o último padrão.** Chumbar modelo dentro do plano é exatamente o erro que essa
ordem evita — o ajuste viraria uma edição de arquivo em vez de um toggle.

## Notas de implementação

Decisões que parecem estranhas e não são:

- **`diz()` em vez de `print()`** — o console do Windows é cp1252; acento e
  travessão quebram o `print` no meio de uma resposta.
- **`max_tokens` generoso** — nos modelos com raciocínio, um teto curto devolve
  resposta **vazia**: o raciocínio consome a cota inteira antes do primeiro
  token de saída.
- **Clones numerados** (`agy2`, `codex2`) — segunda janela do mesmo provedor.
  `base()` resolve o nome, mas checando contra a lista de originais, para não
  decapitar um provedor que legitimamente termine em número.
- **`allow_reuse_address = False` no painel** — no Windows o `SO_REUSEADDR`
  deixa dois servidores subirem na mesma porta, e o segundo atende metade dos
  pedidos. Melhor falhar na largada.
- **`opencode` fora do lote padrão** — a conta foi bloqueada por usar o modelo
  gratuito fora da plataforma deles. É limite de termo de uso, não de volume,
  então rodar menos não resolve. Entra só se pedido à mão, e sob risco conhecido.

## Licença

MIT.
