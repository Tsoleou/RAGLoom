# RAGLoom 節點 Input / Output 對照表

> 每個節點都給「**輸入 → 吐出**」的具體範例,並標上 Python **資料型別**(`str` / `int` / `dict` / `list[...]`)。
> 全篇用同一個問題 `有沒有 1kg 以下的筆電?` 一路接力。
> 真實來源:`api/node_registry.py`(handle 定義)與 `core/*.py`(資料結構)。

---

## 0. 線路上流動的資料型別總覽

| 型別名 (data_type) | Python 型別 | 範例 |
|---|---|---|
| `query` | `str` | `"有沒有 1kg 以下的筆電?"` |
| `documents` | `list[Document]`（`Document = {content: str, metadata: dict}`） | `[Document(content="# StarForge…", metadata={…})]` |
| `chunks` | `list[Chunk]`（`Chunk = {text: str, metadata: dict}`） | `[Chunk(text="StarForge 14 重 1.2kg", metadata={…})]` |
| `embeddings` | `list[list[float]]` | `[[0.12, -0.04, …768個…], …]` |
| `collection` | `chromadb.Collection`（物件,非文字） | `<Chroma Collection "rag_collection">` |
| `results` | `list[RetrievalResult]`（`= {chunk: Chunk, score: float, distance: float}`） | `[RetrievalResult(chunk=…, score=0.82, distance=0.18)]` |
| `reference` | `str` | `"StarForge 14, 1.2kg\nNovaPad, 0.9kg"` |
| `product_id` | `str`（可能為空 `""`） | `"starforge"` 或 `""` |
| `prompt` | `dict`（`{"system": str, "user": str}`） | `{"system":"[內部知識]…", "user":"多重?"}` |
| `system_prompt` | `str` | `"You are a product specialist…"` |
| `format_hint` | `str | dict`（空字串=純文字;dict=JSON schema） | `""` 或 `{…schema…}` |
| `answer` | `str` | `"StarForge 14 重 1.2kg。"` |
| `judge_trace` | `list[dict]` | `[{"i":0, "keep":True, "reason":"…", "source":"…", "score":0.82}]` |
| `metric`（eval） | `dict`（`{"name": str, "score": float\|None, "details": dict}`） | `{"name":"coverage", "score":1.0, "details":{"hit":True, "rank":2}}` |

> 兩端 data_type 一樣才能接。`results` 只能接到吃 `results` 的 input。

---

## 1. Ingest 鏈(灌知識進向量庫)

```
Loader → Chunker → Embedder ↘
                   Chunker  → VectorStore
```

### Loader(載入檔案)
- **▼ 輸入**:(無連線)param `source_path` **`str`** = "./knowledge_base"
- **▲ 吐出** `documents` **`list[Document]`**:
  ```
  [Document(content="# StarForge 14\n重量 1.2kg\nGPU RTX 4060…", metadata={"filename":"starforge.md"})]
  ```

### Chunker(切塊)　param `chunk_size` **`int`**、`chunk_overlap` **`int`**
- **▼ 輸入** `documents` **`list[Document]`**:
  ```
  [Document(content="# StarForge 14\n重量 1.2kg…", metadata={"filename":"starforge.md"})]
  ```
- **▲ 吐出** `chunks` **`list[Chunk]`**:
  ```
  [Chunk(text="StarForge 14 重 1.2kg,鎂鋁合金機身", metadata={"section_title":"規格"}),
   Chunk(text="GPU 為 RTX 4060,適合遊戲與創作",   metadata={"section_title":"效能"})]
  ```

### Embedder(向量化)
- **▼ 輸入** `chunks` **`list[Chunk]`**:
  ```
  [Chunk(text="StarForge 14 重 1.2kg…"), Chunk(text="GPU 為 RTX 4060…")]
  ```
- **▲ 吐出** `embeddings` **`list[list[float]]`**:
  ```
  [[0.12, -0.04, 0.88, …共768個 float…], [0.07, 0.31, -0.22, …共768個 float…]]
  ```

### VectorStore(向量庫)
- **▼ 輸入** `chunks` **`list[Chunk]`** ＋ `embeddings` **`list[list[float]]`**:
  ```
  chunks=[Chunk(text="StarForge 14 重 1.2kg…"), …]
  embeddings=[[0.12, -0.04, …], …]
  ```
- **▲ 吐出** `collection` **`chromadb.Collection`**:
  ```
  <Chroma Collection "rag_collection",已存 2 筆>
  ```

---

## 2. Query 鏈(問問題 → 產生答案)

主鏈,全部用 `有沒有 1kg 以下的筆電?` 接力:
```
QueryInput → Guardrail → PriceGuard → Retriever → RetrievalJudge → ScopeGate
           → ConstraintFilter → PromptBuilder → Generator → OutputCritic → ResultDisplay
```

### QueryInput(問題輸入)
- **▼ 輸入**:(無連線)param `question` **`str`**
- **▲ 吐出** `query` **`str`**:`"有沒有 1kg 以下的筆電?"`

### Guardrail(關鍵字攔截)
- **▼ 輸入** `query_in` **`str`**:`"有沒有 1kg 以下的筆電?"`
- **▲ 吐出** `query_out` **`str`**(沒踩線 → 原樣):`"有沒有 1kg 以下的筆電?"`
- ⚠️ 反例:輸入 `"Asus 的筆電好嗎?"` → 吐出**拒絕訊息**(`str`,流程中止)
- 💡 input `query_in` / output `query_out` 故意不同名(避免前端 DOM id 撞號)

### PriceGuard(價格攔截)
- **▼ 輸入** `query_in` **`str`**:`"有沒有 1kg 以下的筆電?"`
- **▲ 吐出** `query_out` **`str`**(沒問價格 → 原樣):`"有沒有 1kg 以下的筆電?"`
- ⚠️ 反例:輸入 `"StarForge 多少錢?"` → 吐出**拒絕訊息**(`str`,流程中止)

### Retriever(檢索)　param `top_k` **`int`**、`score_threshold` **`float`**
- **▼ 輸入** `query` **`str`** ＋ `collection` **`chromadb.Collection`**:
  ```
  query="有沒有 1kg 以下的筆電?"
  collection=<Chroma Collection "rag_collection">
  ```
- **▲ 吐出** `results` **`list[RetrievalResult]`**:
  ```
  [RetrievalResult(chunk=Chunk(text="NovaPad 重 0.9kg,輕巧便攜"), score=0.82, distance=0.18),
   RetrievalResult(chunk=Chunk(text="StarForge 14 重 1.2kg"),    score=0.79, distance=0.21)]
  ```

### RetrievalJudge(LLM 重排)
- **▼ 輸入** `query` **`str`** ＋ `results_in` **`list[RetrievalResult]`**:
  ```
  query="有沒有 1kg 以下的筆電?"
  results=[NovaPad 0.9kg(0.82), StarForge 1.2kg(0.79)]
  ```
- **▲ 吐出** `results_out` **`list[RetrievalResult]`** ＋ `judge_trace` **`list[dict]`**:
  ```
  results=[NovaPad 0.9kg, StarForge 1.2kg]
  judge_trace=[{"i":0, "keep":True, "reason":"提到重量", "source":"novapad.md",   "score":0.82},
               {"i":1, "keep":True, "reason":"提到重量", "source":"starforge.md", "score":0.79}]
  ```

### ScopeGate(主題範圍守門)
- **▼ 輸入** `results_in` **`list[RetrievalResult]`** ＋ `query` **`str`**:
  ```
  query="有沒有 1kg 以下的筆電?"
  results=[NovaPad 0.9kg, StarForge 1.2kg]
  ```
- **▲ 吐出** `results_out` **`list[RetrievalResult]`**(在主題 → 原樣):`[NovaPad 0.9kg, StarForge 1.2kg]`
- ⚠️ 反例:輸入 `query="今天天氣如何?"` → 吐出**拒絕訊息**(`str`,流程中止)

### ConstraintFilter(數值約束過濾,純 code 無 LLM)
- **▼ 輸入** `query` **`str`** ＋ `results_in` **`list[RetrievalResult]`** ＋ `reference_in` **`str`**:
  ```
  query="有沒有 1kg 以下的筆電?"        ← 抽出條件 weight < 1.0kg
  results=[NovaPad 0.9kg, StarForge 1.2kg]
  reference="NovaPad,0.9kg\nStarForge,1.2kg"
  ```
- **▲ 吐出** `results_out` **`list[RetrievalResult]`** ＋ `reference_out` **`str`**:
  ```
  results=[NovaPad 0.9kg]      ← StarForge 1.2kg 超標，砍掉！
  reference="NovaPad,0.9kg"
  ```

### PromptBuilder(組裝提示詞)
- **▼ 輸入** `query` **`str`** ＋ `results` **`list[RetrievalResult]`** ＋ `reference_data` **`str`**:
  ```
  query="有沒有 1kg 以下的筆電?"
  results=[NovaPad 0.9kg]
  reference="NovaPad,0.9kg"
  ```
- **▲ 吐出** `prompt` **`dict`**(`{"system": str, "user": str}`):
  ```
  {
    "system": "[Internal Knowledge]\nNovaPad 重 0.9kg…\n\n[Reference]\nNovaPad,0.9kg",
    "user":   "有沒有 1kg 以下的筆電?"
  }
  ```

### SystemPrompt(人設,旁支)　param `preset` **`str`**
- **▼ 輸入**:(無連線)param `preset` **`str`** = "professional"
- **▲ 吐出** `system_prompt` **`str`** ＋ `format_hint` **`str | dict`**:
  ```
  system_prompt="You are a product specialist… 回答只用內部知識，勿捏造規格。"
  format_hint=""        ← 空字串 = 純文字
  ```

### ReferenceLoader(常駐參考資料,旁支)
- **▼ 輸入**:(無連線)param `source_path` **`str`** = "./knowledge_base/_reference"
- **▲ 吐出** `reference` **`str`**:
  ```
  "StarForge 14, 1.2kg, 14吋\nNovaPad, 0.9kg, 13吋\nTitanBook, 1.5kg, 16吋"
  ```

### ProductSelector(產品分流,旁支)
- **▼ 輸入** `query` **`str`** ＋ `collection` **`chromadb.Collection`** ＋ `reference_data` **`str`**:`query="有沒有 1kg 以下的筆電?"`(沒指名產品)
- **▲ 吐出** `product_id` **`str`**(沒明確對象 → 空字串):`""`
- 💡 反例:輸入 `query="StarForge 多重?"` → 吐出 `"starforge"`(`str`)

### Generator(生成答案)⭐　param `model` **`str`**
- **▼ 輸入** `prompt` **`dict`** ＋ `system_prompt` **`str`** ＋ `format_hint` **`str | dict`**:
  ```
  prompt={"system":"[Internal Knowledge]\nNovaPad 重 0.9kg…", "user":"有沒有 1kg 以下的筆電?"}
  system_prompt="You are a product specialist…"
  format_hint=""
  ```
- **▲ 吐出** `answer` **`str`**(**純文字!不是 JSON**):
  ```
  "推薦 NovaPad,它重 0.9kg,在 1kg 以下,輕巧便攜,適合外出攜帶。"
  ```
- 💡 內部回 `GenerationResult{text: str, messages: list, model: str}`,線上的 `answer` 就是 `text`。設 `format_type="json"` 才吐 JSON 字串。

### OutputCritic(品管/改寫)⭐
- **▼ 輸入** `answer_in` **`str`** ＋（選用)`query` **`str`** / `retrieval` **`list[RetrievalResult]`** / `reference_data` **`str`**:
  ```
  answer="推薦 NovaPad,它重 0.9kg,在 1kg 以下…"
  query="有沒有 1kg 以下的筆電?"
  retrieval=[NovaPad 0.9kg]
  ```
- **▲ 吐出** `answer_out` **`str`**(沒違規 → 原樣):
  ```
  "推薦 NovaPad,它重 0.9kg,在 1kg 以下,輕巧便攜,適合外出攜帶。"
  ```
- ⚠️ 審查 LLM 內部回 JSON(`dict`)→ `{"pass": True, "reason": "Grounded answer."}`(由 `_extract_json` 撈出)。
  若踩線 → `{"pass": False, "reason": "Mentions competitor brand Asus."}`,mode=revise 時重寫後再吐 `str`。

### ResultDisplay(顯示)
- **▼ 輸入** `answer` **`str`**:`"推薦 NovaPad,它重 0.9kg,在 1kg 以下…"`
- **▲ 吐出**:(無 output)→ 直接渲染到畫面給使用者看

---

## 3. Eval 節點(只在 Editor,ChatView 不用)

### EvalCaseLoader(載入測試案例)
- **▼ 輸入**:(無)param `case_id` **`str`** = "starforge_x1_gpu_en"
- **▲ 吐出**(四個 output 全是 **`str`**):`query` / `expected_product="starforge_x1"` / `expected_facts="RTX 4060\n8GB VRAM"` / `match_mode="all"`

### CoverageMetric(Hit@K)
- **▼ 輸入** `results` **`list[RetrievalResult]`** ＋ `expected_product` **`str`**
- **▲ 吐出** `metric` **`dict`**(`{"name", "score", "details"}`):`{"name":"coverage", "score":1.0, "details":{"hit":True, "rank":2, "top_k":5, "expected_product":"starforge_x1"}}` ← 期望產品出現,排第 2

### ScoreDistributionMetric(分數分布)
- **▼ 輸入** `results` **`list[RetrievalResult]`**
- **▲ 吐出** `metric` **`dict`**(score=None,純描述):`{"name":"score_distribution", "score":None, "details":{"min":0.41, "max":0.82, "mean":0.63, "std":0.14, "top1":0.82, "topk":0.41, "gap_top1_topk":0.41}}`

### DiversityMetric(產品多樣性)
- **▼ 輸入** `results` **`list[RetrievalResult]`**
- **▲ 吐出** `metric` **`dict`**(score=正規化熵):`{"name":"diversity", "score":0.92, "details":{"unique_products":3, "entropy":1.46, "entropy_normalized":0.92, "distribution":{"novapad":1,"starforge":1,"titanbook":1}}}`

### FactsCoverageMetric(事實覆蓋)
- **▼ 輸入** `results` **`list[RetrievalResult]`** ＋ `expected_facts` **`str`** ＋ `match_mode` **`str`**
- **▲ 吐出** `metric` **`dict`**(score=命中比例):`{"name":"facts_coverage", "score":0.5, "details":{"matched":["RTX 4060"], "missing":["8GB VRAM"], "match_mode":"all"}}` ← 兩事實命中一個

### EvalReport(匯總報告)
- **▼ 輸入**:最多 4 個 `metric` **`dict`**(coverage / score_distribution / diversity / facts_coverage)
- **▲ 吐出** `answer` **`str`**(markdown):`"## Eval Report\n- Hit@K: ✅ rank 2\n- Facts: 1/2…"`

### JudgeTraceInspector(觀察 RetrievalJudge)
- **▼ 輸入** `judge_trace` **`list[dict]`**:`[{"i":0, "keep":True, "reason":"提到重量", "source":"novapad.md", "score":0.82}, …]`
- **▲ 吐出**:(無 output)→ 只顯示去留理由

---

## 4. 一眼看懂的「資料接力」全圖(標型別)

```
[硬碟檔案]
   │ documents : list[Document]
 Loader ─► Chunker ─chunks: list[Chunk]─► Embedder ─embeddings: list[list[float]]─┐
                  └────chunks: list[Chunk]──────────────► VectorStore ─collection─┐
                                                                                  │
[使用者問題]                                                                       │
   │ query : str                                                                  │
QueryInput ─► Guardrail ─► PriceGuard ─query: str─► Retriever ◄─collection────────┘
                                              ▲ product_id: str
                              ProductSelector ┘
                                              │ results: list[RetrievalResult]
                          RetrievalJudge ─results─► ScopeGate ─results─► ConstraintFilter
                                                                              │ results + reference: str
            ReferenceLoader ─reference: str──────────────────────────────► PromptBuilder
                                                                              │ prompt: dict
                  SystemPrompt ─system_prompt: str / format_hint─────────► Generator
                                                                              │ answer: str (純文字!)
                                                                         OutputCritic ─answer: str─► ResultDisplay
```

---

## 速記重點

- **大多數線都是 `str`**:query / reference / system_prompt / answer / product_id 全是字串。
- **三種「容器」型別**:`results`=`list[RetrievalResult]`、`chunks`=`list[Chunk]`、`prompt`=`dict`。
- **Generator 吐 `str`**,**Critic 內部判決是 `dict`**(`{"pass",...}`)—— 兩者格式相反。
- **eval 節點的 metric 全是 `dict`**。
- **Guard 類會「短路」**:踩線就中止,改吐拒絕訊息(仍是 `str`)。
