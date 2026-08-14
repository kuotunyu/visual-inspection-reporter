# visual-inspection-reporter — v1 實作計畫

> 狀態（2026-07-15）：**v1 核心（M0–M6、M9、M10）完成**。M7 Claude 與 M8 Gemini
> Batch API 的實作及離線契約測試完成，但尚未取得真實帳戶成功 E2E，故明確標為
> **Experimental**，不納入核心完成宣稱。
> M3 檢查點後使用者授權全權處理，品質迭代（裁切放大 + prompt v2）與 M4–M6 一次做完。
> 實作中的決策補記：
> - `gemini-3.1-flash-lite` 對細微殘銅會誤判誤檢（放大裁切也一樣）；
>   `gemini-3.5-flash` 對照實測全對但貴 ~15 倍 → 預設維持 lite，README 記錄取捨。
> - OpenAI adapter 用 Responses API（`responses.parse` + `text_format`），
>   `gpt-5.4-nano` 單張實測通過。
> - Gradio 為 v6（theme/css 移到 launch()），app 已驗證 HTTP 200。
> - **M7 Claude（Experimental）**：`messages.parse(output_format=...)` + `.parsed_output`；
>   多模態 payload、schema、usage 與解析錯誤已有 mock SDK 離線測試。實際請求曾到達 API，
>   但測試帳戶 credit 不足，尚未取得成功 live 回應。
> - **M8 Gemini Batch API（Experimental）**：`InlinedRequest`/`InlinedResponse` 用 `metadata` 對應，不
>   依賴回傳順序。程式碼已對照實際安裝的 SDK 型別，建單、輪詢、metadata 對應、部分
>   失敗與漏回應都有 mock SDK 離線測試。可用測試帳戶曾回 429 或 400
>   `FAILED_PRECONDITION`，尚未取得成功 live job，故標為 **Experimental**。
> - **M9 report.html**：靠 `markdown` 套件轉換既有 report.md，套用 DESIGN.md 同一份
>   OKLCH 深色 token，避免報告內容邏輯重複一份。
> - **M10 --domain uav**：`detector.py`/`findings.py`/`pipeline.py`/`report.py` 全部
>   改吃 `DomainProfile`（權重/類別表/prompt/報告詞彙），不再寫死 PCB 專屬常數；
>   `findings.py` 的標註顏色改成依 `class_id` 位置取色，不用逐類別維護色表。實測換上
>   uav-traffic-vision 的 YOLO26s VisDrone 10 類權重（ONNX 輸出同樣是 (1,300,6)，
>   letterbox/postprocess 完全不用改），182 個物件的密集路口圖一次 API 呼叫評估完畢，
>   VLM 正確依照新 prompt 給出「巡邏風險」語意而非「瑕疵嚴重度」語意。
> - **retry.py 修正**：UAV 測試時實測撞到 429，發現 google-genai 的建議重試秒數
>   （`google.rpc.RetryInfo.retryDelay`）放在 JSON body 而非 HTTP 標頭，原本的
>   `_retry_after_seconds` 只看標頭會漏掉——已補上解析，兩種來源都吃。
> - **發布前素材整改（2026-08-14）**：原本 `assets/demo.gif` 與 `assets/sample_annotated.jpg`
>   是 HRIPCB 影像的轉換後衍生物，而該資料集授權標示為 Unknown，不適合隨公開 repo 散布。
>   改為：`scripts/make_synthetic_pcb.py` 自製合成板面（附 ground-truth manifest）、
>   `scripts/render_sample_assets.py` 重現標註示範圖、`assets/ui_workstation.png` 為本機
>   介面截圖；GIF 移除。同時實測記錄一個誠實邊界：偵測權重對合成板面有 domain shift
>   （每個銲墊都被判成 missing_hole，最高 conf 0.83），故合成 fixture 只驗流程、
>   不用來宣稱偵測品質。
> 本檔為工作計畫，隨討論持續修訂。

## Context

求職作品集專案：把已訓練好的 YOLO26 PCB 瑕疵偵測模型（本地 ONNX Runtime 推論）與商用 VLM API 串成「產線巡檢報告產生器」——輸入一批 PCB 影像，輸出一份繁體中文巡檢報告（report.md）。展示重點是 **API 工程與系統整合**（供應商抽象、結構化輸出、快取、重試、成本控管、併發），不是再訓練模型。先用 5 張圖的小批次打通全流程並由使用者驗收報告品質，再補完整功能。

## 已查證事實（2026-07-08，實作時如相隔久遠需再確認）

### VLM 模型與定價（USD / 1M tokens，官方定價頁）
| 模型 ID | 狀態 | Input | Output | 備註 |
|---|---|---|---|---|
| `gemini-3.1-flash-lite` | GA | $0.25 | $1.50 | **預設**（最新 flash-lite 級） |
| `gemini-2.5-flash-lite` | GA | $0.10 | $0.40 | 省錢備選 |
| `gemini-3.5-flash` | GA | $1.50 | $9.00 | 品質升級備選 |
| `gpt-5.4-nano` | GA | $0.20 | $1.25 | OpenAI adapter 預設 |
| `gpt-5.4-mini` | GA | $0.75 | $4.50 | OpenAI 品質備選 |

來源：https://ai.google.dev/gemini-api/docs/pricing 、https://developers.openai.com/api/docs/pricing 。價格表連同查證日期寫進 `config.py` 註解。Gemini Batch 定價已納入成本模組，但該路徑仍是 Experimental，live E2E 邊界見檔首狀態。

### SDK
- Gemini：`google-genai` 套件（不是已棄用的 `google-generativeai`）。結構化輸出：`GenerateContentConfig(response_schema=<Pydantic model>, response_mime_type="application/json")`，回傳 `response.parsed`；token 用量在 `response.usage_metadata`。
- OpenAI：`openai` 套件，vision + structured outputs（json_schema）。實作 M4 時照 CLAUDE.md 規範再上官方文件確認當前 API 形態。

### 上游模型（來自同作品集的 `pcb-defect-detection` 專案）
- YOLO26n，6 類（id 順序）：`missing_hole, mouse_bite, open_circuit, short, spur, spurious_copper`；imgsz 640。
- ONNX：`exports\best.onnx`（37MB，batch=1、static、簡化過）。**輸出 `(1, 300, 6)` = [x1,y1,x2,y2,conf,cls]，end-to-end 免 NMS**，座標在 letterbox 後的 640 空間，需反變換回原圖。後處理只要信心過濾 + 座標還原。
- 可重用程式碼（torch-free）：`src\pcb_defect\e2e_onnx.py` — `letterbox()`（cv2、PAD=114，勿用 PIL resize）、`preprocess()`、`postprocess()`、`OnnxYoloModel`。移植時註明出處。
- 權重亦發佈於 HF：`steven0226/pcb-defect-detection`（README 引用此連結作為重現途徑）。
- 測試圖：`data\pcb\images\test\`（board 04，120 張，檔名如 `04_missing_hole_01.jpg`）。資料集 HRIPCB（Kaggle `akhatova/pcb-defects`，授權 unknown，引用 Huang & Wei 2019）。
- 已知弱點：`short` AP50 僅 0.565、`spurious_copper` 0.793 → 報告與 README 要誠實揭露「VLM 只評估已偵測到的項目」。
- 上游 CPU ONNX p50 ≈ 81ms/張（README 取捨說明可引用此實測數字）。
- 上游授權 AGPL-3.0（ultralytics 傳染）→ 本專案移植其推論碼 + 使用其權重，**LICENSE 也用 AGPL-3.0**。

## 已確認決策（使用者 2026-07-08 拍板）
1. 預設模型：`gemini-3.1-flash-lite`。
2. 供應商層：**Gemini + OpenAI 雙後端**都實作（Anthropic 留介面不實作）。
3. 執行環境：**WSL**（照 CLAUDE.md；uv、HF 憑證都在 WSL 內）。指令走 `wsl -d Ubuntu -- bash -lc "cd /mnt/c/... && ..."`。

## 對原始規格的修改（已納入設計）
1. **CLI 檔名 `inspect.py` → `inspect_cli.py`**：`inspect` 是 Python stdlib 模組名，`python inspect.py` 會把腳本目錄放到 sys.path 首位，pydantic 等套件 `import inspect` 時會誤載本腳本而炸掉。
2. **無瑕疵影像跳過 VLM 呼叫**（config `vlm_on_clean=False`）：report 直接標「合格（未檢出瑕疵）」模板文字。這是最大的成本槓桿之一。
3. **VLM 輸入用「標註過的整圖」而非原始整圖**：整圖上畫編號框（#1..N，對應 findings JSON 的 id），最長邊縮到 1280px；裁切圖帶 context margin（bbox 外擴 1.6 倍、最短邊至少 64px、不超出原圖），每圖裁切上限 8 張（依信心排序，其餘僅留在 JSON）。編號讓 VLM 的回覆可以被 schema 強制對齊到具體 finding，防幻覺。
4. **嚴重度與判定用固定 enum**：severity ∈ {critical, major, minor}、每圖 verdict ∈ {pass, rework, fail}，渲染時映射繁中（重大/中等/輕微；合格/需複檢/不合格）。enum 定義與判斷準則寫進 prompt。
5. **解析防呆**：VLM 回傳的 finding_id 必須是輸入 id 的子集——未知 id 丟棄並記 warning；缺漏的 finding 標「未評估」，不捏造。
6. **成本統計用 API 回傳的實際 usage**（不是自行估算）；快取命中計 0 成本並在報告註明命中數。USD→NTD 匯率為 config 常數（實作時查一次當時匯率寫入，註明日期、手動維護）。開發驗證走 Gemini **免費層**（實際帳單 $0），報告與 README 的成本一律標明「以付費層官方定價換算的估算值」。
7. **雙輸出**：`report.md` + `report.json`（機器可讀，利於測試與後續擴充）。
8. **單圖失敗不炸整批**：per-image try/except，錯誤記錄進報告該圖條目。
9. **v0 smoke 順序調整**：先做「同步、低併發、含快取」的最小路徑跑 5 張圖給使用者驗收（快取要先做——調報告格式時反覆重跑不重扣 API 費）；重試/併發/OpenAI adapter/Gradio/pytest 全部放在驗收之後。

## Repo 結構

repo 根目錄為 `visual-inspection-reporter/`；支援在 Windows 或 WSL 從該目錄執行 `uv` 指令，不記錄個人電腦的絕對路徑。

```
visual-inspection-reporter/
├── pyproject.toml            # uv；py>=3.11；hatchling（比照專案 1）
├── plan.md                   # 本計畫（隨討論更新）
├── README.md  LICENSE(AGPL-3.0)  .gitignore  .env(從上層複製，不進 git)
├── weights/                  # gitignored；best.onnx 從專案 1 exports/ 複製
├── sample_images/            # gitignored（資料集授權 unknown）；附 fetch 說明
├── scripts/                  # 合成 fixture 產生器 + README 素材重現腳本
├── assets/                   # 進 git：僅自製素材（合成標註圖、UI 截圖、純文字範例報告）
├── src/inspector/
│   ├── config.py             # 模型/價格表(含查證日期註解)、匯率、閾值、併發數、prompt 版號
│   ├── detector.py           # ONNX Runtime；移植 e2e_onnx.py（letterbox/preprocess/postprocess）
│   ├── findings.py           # Finding 組裝：裁切(含 margin)、編號標註整圖、findings JSON
│   ├── schema.py             # Pydantic：ImageAssessment/FindingAssessment/Severity/Verdict + 防呆驗證
│   ├── providers/
│   │   ├── base.py           # VLMProvider ABC：assess_image(request) -> (ImageAssessment, Usage)；factory
│   │   ├── gemini.py         # google-genai, response_schema
│   │   └── openai_provider.py# openai SDK, json_schema（M4）
│   ├── cache.py              # sha256(影像bytes+偵測JSON+prompt版+model+schema版) → .cache/vlm/{key}.json
│   ├── cost.py               # usage 累計 → USD/NTD
│   ├── retry.py              # tenacity 指數退避+jitter（429/5xx/timeout，尊重 Retry-After，最多5次）（M4）
│   ├── pipeline.py           # 批次流程；ThreadPoolExecutor(可調，預設4)；單圖錯誤隔離
│   └── report.py             # report.md / report.json 渲染
├── inspect_cli.py            # argparse：--input-dir --output --provider --model --conf --max-workers --no-cache --detect-only
├── app.py                    # Gradio：多檔上傳 → 進度條 → Markdown 報告 + 標註圖 gallery + 下載
└── tests/                    # findings 組裝、schema 解析防呆、cache 鍵、report 渲染、cost 計算（MockProvider，零網路）
```

### report.md 版型
執行資訊（時間/模型/張數）→ 總覽統計（verdict 計數、瑕疵類別分佈、嚴重度分佈表）→ 逐圖明細（標註圖、findings 表格：編號/類別/信心/嚴重度/繁中說明/建議處置、該圖 VLM 總評與 verdict）→ 附錄：token 用量與成本（各模型 input/output tokens、USD、NTD、匯率假設、快取命中數）。圖片用輸出目錄內相對路徑。

### VLM Prompt（v1，版號進快取鍵）
繁中系統指示：角色 = PCB 產線品管工程師；給 severity/verdict 判斷準則；輸入 parts = 標註整圖 + 各裁切圖（標明 finding id）+ 偵測 JSON（id/類別/信心/歸一化 bbox）；要求全部輸出繁體中文。

## 里程碑（每個里程碑結尾 git commit，英文訊息）

- **M0 腳手架**：git init、.gitignore（weights/ sample_images/ .cache/ output/ .env *.onnx *.pt）、pyproject（deps：onnxruntime、opencv-python-headless、numpy、pillow、google-genai、openai、pydantic、python-dotenv、tenacity、gradio；dev：pytest、ruff）、複製 .env 與 best.onnx、從 board04 test 挑 5 張不同瑕疵類別的圖進 sample_images/、plan.md、README 骨架。
- **M1 偵測模組**：移植 detector + findings（標註圖/裁切/JSON）；`inspect_cli.py --detect-only` 跑 5 張圖；目測標註圖驗證（留意 ultralytics#23397 的 ONNX 重疊框問題；上游 parity 已驗過 mAP 差 <3%，風險低）。
- **M2 VLM 層（Gemini）**：**先打一發最小請求實測 `gemini-3.1-flash-lite` 在免費層是否可用**（AI Studio 額度清單未能確認該型號；不可用則暫退 `gemini-2.5-flash-lite` 並回報使用者）；schema.py、gemini.py（實作前照 CLAUDE.md 再確認 google-genai quickstart）、cache.py、cost.py；單張圖端到端手動驗證。
- **M3 v0 smoke + 報告** ★檢查點：pipeline（同步、workers=2）+ report.md 渲染；跑 5 張圖產出完整報告；**停下來給使用者驗收報告品質**（措辭、severity 準則、版型），依回饋迭代（快取讓迭代近乎免費）。
- **M4 工程完整化**（驗收後）：retry.py、併發參數化 + **客戶端 RPM 限速器**（config `max_rpm` 預設 8，依開發期低額度帳戶的觀察保留餘裕；實際 quota 依帳戶調整，或設 0 停用）、OpenAI adapter（`--provider openai` 實測 1–2 張）、report.json、錯誤隔離補強。
- **M5 測試 + Gradio**：pytest 全套（MockProvider）、app.py。
- **M6 文件收尾**：README（mermaid 架構圖、範例報告截圖入 assets/、**以實測 usage 換算每 100 張成本表**——CLAUDE.md 嚴禁捏造數字、「自訓小模型偵測 + API 大模型理解」取捨說明引用上游實測 CPU p50 81ms 與定價表、重現步驟、HF 權重連結、HRIPCB 出處與授權聲明、AGPL 說明）；ruff + pytest 全綠。**推 GitHub 前先跟使用者確認 repo 名稱與可見性。**

### 原規劃延後項目（目前狀態）

- `--domain uav`、報告 HTML：已完成並納入核心。
- Anthropic adapter、Gemini Batch API：程式碼與離線測試完成，live 成功 E2E 待具備合適帳戶條件後補驗，現階段為 Experimental。

## 驗證方式
1. `uv run pytest`（WSL）全綠；provider / Batch 使用 mock SDK，測試零網路、零 API 費用。
2. `uv run python inspect_cli.py --input-dir sample_images --output output/ --detect-only`：標註圖框位正確。
3. 同指令去掉 `--detect-only`：產出 report.md/report.json，繁中內容、成本附錄有非零 usage；**立刻重跑一次**，快取全命中、成本 ≈ $0。
4. `--provider openai` 抽測 1 張（M4 後）。
5. `uv run python app.py` → Windows 瀏覽器開 localhost:7860，拖 5 張圖看線上報告。
6. Experimental live 驗證（非核心完成條件）：以有足夠 Claude credit 的帳戶跑一張；以已允許 Batch 的 Gemini 專案提交小批次，保存成功 job 與 report 作為證據。

## 風險與注意
- `.env` 只複製、絕不 commit；程式一律 python-dotenv 讀取。
- Gemini 額度依帳戶、專案與方案而異；開發期實際撞過 429，因此保留 `--max-rpm` 與 RetryInfo 退避。README 不把某一測試帳戶的額度寫成所有使用者都適用的保證。
- API key 只放 `.env`，不在 tracked 文件留下帳戶名稱、project 名稱或 key 片段。多支 key 也可能共用同一 project 額度；live 驗證前應先確認 key 所屬專案與計費風險。
- Gemini Batch 的可用性受帳戶/專案條件影響；目前沒有符合條件且已授權產生費用的測試帳戶，因此不為了補作品集證據擅自綁定計費。
- 免費層輸入可能被 Google 用於產品改進：本專案只餵公開資料集（HRIPCB）影像，無敏感性問題，README 註記一句即可。
- 成本量級（僅設計參考，README 不用估值）：每張約 2–4k input tokens（圖佔大宗）+ ~0.5k output，flash-lite 級 100 張約 $0.1–0.5 USD——快取與跳過無瑕疵圖是主要槓桿。
- 資料集圖片不進 git；README 說明如何從 Kaggle/專案 1 取得。
