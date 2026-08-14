# visual-inspection-reporter

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

> PCB 產線巡檢報告產生器：本地 YOLO26 ONNX 偵測 + 商用 VLM API → 繁體中文巡檢報告

![Gradio 巡檢工作站：左側控制面板拖入影像、右側顯示編號標註圖與報告](assets/ui_workstation.png)

輸入一批影像，輸出 `report.md`、`report.json` 與可選的 `report.html`：

- **可追溯**：本地 YOLO26 ONNX 先產出編號整圖、局部裁切與偵測 JSON，VLM 的每個判斷都必須對回 finding id。
- **可重跑**：供應商抽象、結構化輸出、內容雜湊快取、429/5xx 退避與 RPM 限速都在同一條 pipeline。
- **有邊界**：Gemini／OpenAI 有真實同步 E2E；Claude 與 Gemini Batch 明列 Experimental，不把 mock 驗證寫成 live 成功。

![範例標註圖：六類瑕疵標籤自動避讓，保留 finding id 與類別](assets/sample_annotated.jpg)

上圖是 `scripts/render_sample_assets.py` 對**自製合成 fixture** 跑 `findings.annotate()` 的實際輸出，用來展示標籤避讓與編號排版；框線來自合成腳本自己記錄的 ground truth，**不是模型預測結果**（原因見「侷限」的 domain shift 說明）。本 repo 不散布任何資料集影像，授權說明見「資料集與授權」。

## 完成度與驗證邊界

| 範圍 | 狀態 | 驗證 |
|---|---|---|
| 本地 YOLO 偵測、findings、報告（MD/JSON/HTML）、快取/重試/成本、Gradio | **核心完成** | 離線 pytest + 本機 smoke / 範例輸出 |
| Gemini / OpenAI 同步 provider | **核心完成** | 真實 API 成功回應 + 離線 schema/錯誤處理測試 |
| Claude provider | **Experimental** | SDK 請求格式與離線 mock 測試完成；測試帳戶 credit 不足，尚無成功 live E2E |
| Gemini Batch API | **Experimental** | SDK 型別、metadata 對應、輪詢/部分失敗均有離線 mock 測試；可用帳戶的 live job 尚未成功提交 |

因此預設同步流程可作為 v1 核心成果重現；`--provider claude` 與 `--batch-api` 保留為可試用的實驗功能，不列入核心完成條件。

## 為什麼「偵測用自訓小模型、理解與文字生成用 API 大模型」？

| | 自訓 YOLO26n（本地 ONNX） | 商用 VLM API |
|---|---|---|
| 擅長 | 固定類別的定位與召回，毫秒級、零 API 成本 | 開放式視覺理解、誤檢識別、專業文字生成 |
| 不擅長 | 說明「為什麼有問題、該怎麼處理」 | 精確定位小目標；逐張全圖掃描又貴又慢 |
| 實測 | CPU p50 ≈ 81 ms/張（上游 benchmark） | flash-lite 級 ≈ $0.0027/張（本 repo 實測） |

兩段式分工讓 VLM 只看「已裁好、已標號」的少量像素，token 花在刀口上：偵測負責「哪裡有什麼」，VLM 負責「多嚴重、為什麼、怎麼辦」。VLM 還能反過來抓偵測模型的誤檢——實測中它把絲印文字誤判的 `missing_hole` 全數識破並標註「誤檢，建議人工確認」。

## 架構

```mermaid
%%{init: {'themeVariables': {'fontSize': '18px'}, 'flowchart': {'nodeSpacing': 45, 'rankSpacing': 55}}}%%
flowchart TD
    P["DomainProfile<br/>--domain pcb｜uav"] -.-> B & F
    A[影像資料夾] --> B["YOLO26 偵測<br/>ONNX Runtime"]
    B --> C[findings 組裝]
    C --> D{有偵測到東西？}
    D -- 否 --> I
    D -- 是 --> E[內容雜湊快取]
    E -- 命中 --> G
    E -- 未命中 --> F[VLM 結構化輸出]
    F --> G[解析防呆]
    G --> I["報告輸出<br/>md／json／html"]
```

細節都在下面的「工程細節」——圖只畫主流程，避免節點塞太多字被 GitHub 縮小到看不清楚。

工程細節：

- **供應商抽象**：`VLMProvider` 介面 + factory，`--provider gemini|openai|claude` 共用呼叫介面。Gemini 走 `google-genai` 的 `response_schema`，OpenAI 走 Responses API 的 `responses.parse`，Claude 走 Messages API 的 `messages.parse(output_format=...)`；前兩者有成功 live E2E，Claude 目前為 Experimental。
- **結構化輸出 + 防呆**：Pydantic schema 直接下到 API；回傳的 `finding_id` 必須是偵測 JSON id 的子集——幻覺 id 剔除、漏評 id 在報告標「未評估」，不捏造內容。
- **快取**：鍵 = sha256(原圖 bytes + 偵測 JSON + 模型 + prompt 版本 + schema 版本)。同批重跑成本 $0；改 prompt 自動失效。
- **韌性**：429/5xx/逾時指數退避（最多 5 次），等待秒數取「供應商建議值」與「指數退避」的較大值——一般 SDK 走 HTTP `Retry-After` 標頭，但 google-genai 的 429 把建議秒數放在 JSON body 的 `google.rpc.RetryInfo` 裡（沒有標頭），兩種都有解析，這是實測踩過才補上的）＋滑動窗 RPM 限速（預設 8，對應 Gemini 免費層；`--max-rpm 0` 停用）；單圖失敗記入報告該圖、不炸整批。
- **Gemini Batch API（Experimental）**（`--batch-api`，僅 gemini）：先查快取，剩下的一次送成一個 batch job，輪詢至完成再取回。用 `InlinedRequest`/`InlinedResponse` 的 `metadata` 欄位對應原始請求，不依賴回傳順序；編排與失敗隔離已有離線測試，但真實帳戶成功 job 尚未完成驗證。
- **成本統計**：token 數取 API 回傳的實際 usage，依官方定價（同步或 Batch 5 折）換算 USD 附在報告末尾。

## 範例報告

節錄自 [assets/sample_report.md](assets/sample_report.md)（5 張樣本圖實際輸出）：

> ### 4. 04_short_01.jpg — 判定：不合格
>
> | # | 類別 | 信心 | 嚴重度 | 說明 | 建議處置 |
> |---|---|---|---|---|---|
> | #1 | 短路（short） | 0.66 | 重大 | 走線間出現明顯的銅橋接，造成短路，嚴重影響電氣功能。 | 判定為不合格，需進行報廢或返修評估。 |
> | #2 | 缺孔（missing_hole） | 0.53 | 輕微 | 經檢視局部放大圖，該區域為絲印文字而非鑽孔，模型誤判為缺孔。 | 此項為誤檢，無需處理。 |
>
> **總評**：本板存在多項嚴重瑕疵，包含短路與斷路，直接影響電路功能，判定為不合格。

## 成本實測（2026-07-09，匯率 32.1）

以本 repo 實測 usage 換算（token 數為 API 回傳值，單價為官方付費層定價；免費層實際帳單 $0）：

| 模型 | 實測基礎 | 每張約 | 每 100 張約 |
|---|---|---|---|
| `gemini-3.1-flash-lite`（預設） | 5 張批次：40,604 in / 2,305 out tokens，$0.0136 | $0.0027 | **$0.27 ≈ NT$8.7** |
| `gpt-5.4-nano`（--provider openai） | 1 張：3,871 in / 676 out，$0.0016 | $0.0016 | $0.16 ≈ NT$5.2 |
| `gemini-3.5-flash`（升級複核用） | 1 張：8,819 in / 2,898 out，$0.0393 | $0.0393 | $3.93 ≈ NT$126 |

模型選擇建議：日常巡檢用 flash-lite 級即可；實測發現 lite 級對**細微低對比瑕疵**（如殘銅細線）可能誤判為誤檢，同一張圖 `gemini-3.5-flash` 能正確識別三處殘銅並讀出絲印文字內容——重要批次可用 `--model gemini-3.5-flash` 複核（約 15 倍成本）。

## 跨領域可移植性：`--domain uav`

同一條 pipeline（偵測 → findings 組裝 → VLM 結構化輸出 → 報告）換一組 `DomainProfile`（權重 + 類別表 + prompt + 報告詞彙）就能服務完全不同的任務，不用改 detector/pipeline/report 程式碼一行。實測換成另一個作品集專案 [uav-traffic-vision](https://huggingface.co/steven0226/uav-traffic-vision) 的 YOLO26s VisDrone 權重（10 類：行人/人群/腳踏車/小客車/廂型車/卡車/三輪車/篷布三輪車/公車/機車），輸出「無人機空拍巡邏報告」而非「PCB 巡檢報告」：

```bash
uv run python inspect_cli.py --input-dir my_drone_photos --output output_uav/ --domain uav
```

實測一張 182 個物件的密集路口空拍圖，`gemini-3.1-flash-lite` 一次 API 呼叫全數評估完畢，總評正確抓到「路口車流量大但秩序尚可、無立即安全風險」的情境判斷——PCB 領域的「嚴重度/判定」語意（瑕疵嚴重度、良品/不良品）在 prompt 換成巡邏語意（風險關注程度、是否需通報）後依然運作正常，這就是「domain profile 只換資料與措辭、工程骨架不動」的驗證。

換領域只需要在 `src/inspector/domains.py` 加一個 `DomainProfile`（權重路徑、類別表、prompt、報告詞彙、已知侷限），不用碰 `detector.py`/`pipeline.py`/`report.py`。VisDrone 資料集僅限學術用途，本 repo 不隨附任何無人機影像，需自備測試圖。UAV 權重同樣不隨 repo 發佈，需另外下載到 `weights/`：

```bash
hf download steven0226/uav-traffic-vision yolo26s_visdrone_640.onnx --local-dir weights
```

## 更多輸出格式

```bash
uv run python inspect_cli.py --input-dir sample_images --output output/ --html
```

多產出一份 `report.html`，沿用 Gradio 介面的深色主題色票（見 DESIGN.md），方便直接寄送或用瀏覽器開啟而不需要 Markdown 檢視器。

## 快速開始

需求：[uv](https://docs.astral.sh/uv/)、repo 根目錄 `.env`（`GEMINI_API_KEY=...`，或 `GOOGLE_API_KEY=...` 亦可——`google-genai` 兩者都認，優先採用 `GOOGLE_API_KEY`；用 OpenAI/Claude 則另加 `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`，只用預設 Gemini 的話兩者皆非必要）。

先在目前這個 GitHub repository 頁面按 **Code**，複製 HTTPS URL 並以 Git 或
GitHub Desktop clone。為避免 repository 移轉後文件失效，這裡不硬編碼 owner。

```bash
cd visual-inspection-reporter
uv sync

# 建立本機環境檔後，只填入實際會使用的 provider 金鑰
cp .env.example .env
# Windows PowerShell 可改用：Copy-Item .env.example .env

# 權重（不隨 repo 發佈）：從 Hugging Face 下載 best.onnx 放進 weights/
hf download steven0226/pcb-defect-detection best.onnx --local-dir weights

# 測試影像：不想先去下載資料集的話，直接產一批自製合成板面（授權明確、可重現）
uv run python scripts/make_synthetic_pcb.py --output-dir sample_images

# CLI：批次巡檢
uv run python inspect_cli.py --input-dir sample_images --output output/
uv run python inspect_cli.py --input-dir ... --provider openai          # 換核心 provider（gemini｜openai）
uv run python inspect_cli.py --input-dir ... --provider claude          # Experimental；需另做 live E2E
uv run python inspect_cli.py --input-dir ... --model gemini-3.5-flash   # 換模型
uv run python inspect_cli.py --input-dir ... --detect-only              # 只跑偵測
uv run python inspect_cli.py --input-dir ... --domain uav               # 換領域（見上方「跨領域可移植性」）
uv run python inspect_cli.py --input-dir ... --batch-api                # Experimental；非即時，帳戶條件見侷限
uv run python inspect_cli.py --input-dir ... --html                     # 額外產出 report.html

# Gradio 介面（http://localhost:7860；供應商可切換，但 --domain/--batch-api 目前僅 CLI 提供）
uv run python app.py

# 測試（mock VLM，零網路）
uv run pytest
```

主要參數：`--conf` 偵測閾值（預設依 `--domain`）、`--max-workers` 併發（4）、`--max-rpm` 限速（8，`0` 停用）、`--no-cache` 停用快取。

測試影像有兩種來源：

- **合成 fixture（預設、零下載）**：`scripts/make_synthetic_pcb.py` 用幾何繪製從零合成板面與六類瑕疵，附 ground-truth manifest，輸出與本 repo 同授權。適合驗證整條流程、報告格式與 VLM 串接。
- **真實 PCB 影像（衡量偵測品質時用）**：HRIPCB 原始資料集不隨 repo 發佈，可從 [Kaggle akhatova/pcb-defects](https://www.kaggle.com/datasets/akhatova/pcb-defects) 自行下載後任選幾張放進 `sample_images/`。偵測權重是在這批真實照片上訓練的，只有它能反映真實偵測表現。

## 專案結構

```
inspect_cli.py / app.py          # CLI 與 Gradio 進入點
PRODUCT.md / DESIGN.md           # 產品原則與既有 Gradio 視覺系統
scripts/                         # 合成 fixture 產生器、README 素材重現腳本
src/inspector/
├── config.py                    # 跨領域共用定價表（含查證日期）、閾值、版本號
├── domains.py                   # DomainProfile：pcb｜uav 各自的權重/類別/prompt/報告詞彙
├── detector.py                  # ONNX Runtime 推論（YOLO26 e2e 免 NMS，class_names 由呼叫端傳入）
├── findings.py                  # 編號標註圖（含標籤避讓/邊界 clamp）、context 裁切、偵測 JSON
├── schema.py / prompt.py        # Pydantic 輸出 schema、各領域的繁中巡檢指示
├── providers/                   # gemini.py、openai_provider.py、claude.py、base.py（抽象層）
├── batch_gemini.py              # Gemini Batch API：送出/輪詢/取回（metadata 對應原始請求）
├── cache.py / cost.py / retry.py# 快取、成本統計（同步/Batch 兩種定價）、退避重試＋RPM 限速
├── pipeline.py                  # 批次流程（併發或 Batch API 二選一、單圖錯誤隔離）
└── report.py                    # report.md / report.json / report.html 渲染
tests/                           # 離線 pytest（含 provider / Batch mock，零網路、零 API 費用）
```

## 侷限

- 偵測模型在最誠實的 board-grouped split 下 `short` 類 AP50 僅 0.565、`spurious_copper` 0.793（見[上游專案](https://huggingface.co/steven0226/pcb-defect-detection)），漏檢的瑕疵 VLM 看不到；UAV 領域則是 `awning-tricycle`（AP50-95 0.107）與 `bicycle`（0.124）最弱，且極小物件整體偏低（見 [uav-traffic-vision](https://huggingface.co/steven0226/uav-traffic-vision)）。
- flash-lite 級 VLM 對細微低對比瑕疵有極限（見成本一節的殘銅案例）。
- **合成 fixture 只驗流程、不衡量偵測品質**：偵測權重是在真實 HRIPCB 照片上訓練的，對合成板面有明顯 domain shift——實測把合成板上每個正常銲墊都判成 `missing_hole`（單張 34 個誤檢，最高 conf 0.83，提高閾值也濾不掉）。因此 `scripts/` 產生的合成影像用來跑通 pipeline、報告與供應商串接，偵測準確度一律以上游 [pcb-defect-detection](https://huggingface.co/steven0226/pcb-defect-detection) 在真實測試集的數字為準。
- Gemini 額度依帳戶、專案與方案而異；本 repo 開發期間實際撞過 429（含 Batch 提交），因此保留 `--max-rpm` 與 Google 專屬 RetryInfo 退避。大量處理前請依自己的 quota 調整，README 不保證固定 RPM。
- Claude provider 已驗證 SDK 請求能到達 API，並以 mock 覆蓋多模態 payload、結構化結果、usage 與解析失敗；受限於當時測試帳戶 credit 不足，尚無成功 live 回應，因此標為 **Experimental**。
- Gemini Batch API 已以實際安裝的 SDK 型別及 mock 覆蓋建單、輪詢、metadata 對應、部分失敗與漏回應；可用測試 key 曾回 429 或 400 `FAILED_PRECONDITION`，當時帳戶/專案條件不足，尚未取得成功 live job，因此標為 **Experimental**。要宣稱 production-ready 前仍需在已允許 Batch 的帳戶完成一輪付費風險可控的 E2E。

## 資料集與授權

- 偵測權重與推論程式碼衍生自上游專案 [pcb-defect-detection](https://huggingface.co/steven0226/pcb-defect-detection)（以 ultralytics YOLO26 訓練）。
- **本 repo 不散布任何資料集影像或其衍生影像。** 隨 repo 發佈的圖檔只有兩個，都自製且與 repo 同授權：
  - `assets/sample_annotated.jpg`：`scripts/make_synthetic_pcb.py` 合成的板面 + `scripts/render_sample_assets.py` 渲染的標註，可用 `uv run python scripts/render_sample_assets.py` 重現。
  - `assets/ui_workstation.png`：本機執行 `uv run python app.py` 的介面截圖。
- 資料集：HRIPCB（PKU-Market-PCB），來源 [Kaggle akhatova/pcb-defects](https://www.kaggle.com/datasets/akhatova/pcb-defects)，授權標示為 **Unknown**，引用 [Huang & Wei (2019)](https://arxiv.org/abs/1901.08204)。因授權不明，原始資料、`sample_images/` 與任何由其轉換而來的展示影像一律不進 repo；需要真實影像請自行下載並自負授權責任。
- `assets/sample_report.md` 是本工具對 5 張 HRIPCB 影像的實際文字輸出（報告文字與 token 用量，不含任何影像）；README 成本表的數字即來自這次執行。
- VisDrone（`--domain uav`）僅限學術研究用途，本 repo 同樣不隨附任何無人機影像。
- License：**AGPL-3.0-or-later**（受 ultralytics 授權傳染條款約束）。
