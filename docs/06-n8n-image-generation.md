# 商品图自动化 · 全自动管线（Boss 操作 = 0）

> 状态：v2 重写 · 2026-05-08
> 设计目标：**Boss 只做 2 件事，其他全部 N8N + CMS 自动跑完**
> 工具核心：Boss 已充值的 **Nano Banana 2**（Google Gemini 3.1 Flash Image）

---

## ⭐ TL;DR · Boss 要做的就这 2 件事

### ① 一次性（5 分钟）
打开 CMS page 23「参考图库」→ 把 Boss 想要的详情图风格样本上传上去（按品类
分组，每组 3-14 张）。**之后再也不用碰**。

### ② 日常（30 秒/批）
在 CMS page 21 输 SPU CSV（这是 Boss 现在已经在做的事）→ 点「**全自动出图 +
上架**」一个按钮 → 完事。

**完事**之后 Boss 只在飞书群看通知：
- ✅ "Shopee TW 已上架 12 商品（含 60 张自动生成的详情图）"
- 🟡 "3 商品风格不达标，等 Boss 在飞书点确认或重生"

---

## 🎬 Boss 视角的完整流程

```
Boss              CMS                    N8N (后台 7×24)        Shopee
 │                                                                
 │ ① 上传参考图  ──────────►                                     
 │   (一次性)        page 23 存到 reference-images/             
 │                                                                
 │                                                                
 │ ② 输 SPU CSV  ──────────►                                     
 │   page 21 输入        [自动检测哪些 SPU 缺图]                
 │                          │                                    
 │                          ▼                                    
 │                       插入 image_gen 任务                     
 │                          │                                    
 │                          ▼                                    
 │                                ──────────────►              
 │                          每 30 分钟 cron 扫               
 │                                                          
 │                          ◄──────────────────              
 │                          Nano Banana 2 出 60 张                
 │                          (按 ① 的参考图风格)                  
 │                          │                                    
 │                          ▼                                    
 │                       配回 SPU                              
 │                          │                                    
 │                          ▼                                    
 │                                ──────────────►              
 │                          Shopee mass-upload                  
 │                                                                ──► 商品上架
 │                                                                
 │ ◄──────────────────────────────────────  飞书通知                
 │   "✅ 上架 12 商品 含 60 张自动图"                            
```

**每周 Boss 总操作时间 ≈ 5 分钟**（仅日常的 SPU CSV 录入，参考图永远不用再碰）。

---

## 🧠 工具选型 · Nano Banana 2 一站式

Boss 已充值，**完美匹配本场景**：

| 能力 | 说明 |
|---|---|
| **14 张参考图同时输入** | 一次喂"5 张风格参考图 + 1 张商品白底" → 直接出 Boss 风味的详情图 |
| **风格一致性** | Google 专门优化的卖点（前一代 Nano Banana 就主推此能力）|
| **文字渲染** | 日文/中文 headline + CTA + 价格标 直接画进图（不需要 Bannerbear 模板）|
| **多角度商品镜头** | 一次出 5 张同商品不同角度，省 5 倍 API 调用 |
| **N8N 官方 workflow 已有** | n8n.io/workflows/9712 直接抄 |

**价格**（Boss 已充值，但记一下用量）：

| 输出 | 单价 | Batch（夜间）|
|---|---|---|
| 1024px 标准详情图 | $0.045 | $0.0225 |
| 4K 高清主图 | $0.151 | $0.0755 |

**月度估算**（Boss 现在 SPU 约 50 个/月，每商品 5 张图）：
- 250 张 × $0.045 = **$11/月** → 一杯咖啡的钱

如果 Boss 加大产能到 200 个 SPU/月：
- 1000 张 × $0.045 = **$45/月**

---

## 🏗️ 背后的自动管线（Boss 不用看，留给后续实现）

### 流程组件

| 模块 | 在哪 | 状态 |
|---|---|---|
| 参考图库存储 | `商品信息管理/data/reference-images/<category>/*.png` | 待 v1.2 实现 |
| CMS page 23（上传参考图） | `pages/23_🎨_参考图库.py` | 待实现 |
| CMS page 21 增按钮（全自动出图+上架） | 现 page 21 改造 | 待实现 |
| N8N workflow `image-gen-cron.json` | 30 分钟 cron | 待 v1.2 加 |
| N8N workflow `product-image-nano-banana.json` | webhook 触发版 | 待 v1.2 加 |
| automation_runs 加 `module='image_gen'` 流程 | schema 已支持 | 直接用 |

### Boss 一次性把参考图上传后，CMS 怎么用

```
data/reference-images/
├── ボトル/                    ← 水壶类参考图
│   ├── ref-1.png
│   ├── ref-2.png
│   └── ref-3.png
├── ステッカー/                ← 贴纸类
│   ├── ref-1.png
│   └── ...
└── スマホスタンド/             ← 手机支架
    └── ...
```

CMS 检测到 SPU 类目（用现有 Shopee category mapper）→ 自动从对应文件夹加载所有
参考图 → 喂给 Nano Banana 2。

### N8N cron workflow（核心）

每 30 分钟跑一次，做这件事：

```javascript
// 伪代码
cron("0,30 * * * *")
  → GET /api/automation/queue?module=image_gen&status=pending  (CMS endpoint)
  → for each pending run:
       refs = 加载 data/reference-images/<run.category>/*.png
       product_image = run.payload.product_white_bg_url
       result = POST gemini-3.1-flash-image-preview:generateContent {
         parts: [
           text: "Generate detail image in style of these references...",
           inlineData: refs[0..5],
           inlineData: product_image
         ]
       }
       上传 result 到 data/outputs/images/<run_id>/
       UPDATE automation_runs SET status='completed', summary={...}
       LARK_NOTIFY: "出图完成，链接：..."
       自动触发 shopee_mass_upload module（链路下一段）
```

---

## 📋 v1.2 实施清单（我要做的）

| 任务 | 工作量 | 是否阻塞 Boss |
|---|---|---|
| N8N `product-image-nano-banana.json` workflow | 1h | 不阻塞 |
| N8N `image-gen-cron.json` 7×24 cron | 1h | 不阻塞 |
| CMS page 23 参考图库管理 | 2h | 不阻塞 |
| CMS page 21 加「全自动」按钮 | 1h | 不阻塞 |
| CMS 自动触发逻辑（SPU CSV → 缺图检测 → image_gen 入队）| 2h | 不阻塞 |
| `/api/automation/queue` endpoint（FastAPI sidecar 或 Streamlit query API）| 2h | 不阻塞 |
| 安装包 v1.2 重打 | 0.5h | 不阻塞 |

**全部加起来 < 10h**。我下一轮直接做完，Boss 不用等。

---

## 🚀 Boss 现在要做的（仅 1 件）

把 Nano Banana 2 的 API Key 给我（或者放到 CMS 的 .env 里）：

```
GEMINI_API_KEY=...
```

> 在 [Google AI Studio](https://aistudio.google.com/app/apikey) 复制即可。
> 已充值的额度自动绑定这个 key。

放好之后回我一句"Key 已配置" → 我下一轮直接落地全套，**Boss 啥也不用做**。

---

## 🆘 备选方案（仅 Nano Banana 2 实测出图味道不对时启用）

| 方案 | 何时启用 | 月费（50 SPU/月）|
|---|---|---|
| **A** Nano Banana 2 一站式（默认） | 默认 | **$11** |
| **B** Nano Banana 2 + Bannerbear 模板（要复杂排版）| 详情图需特定版式 | $11 + $49 = $60 |
| **C** fal.ai FLUX + IP-Adapter（如风格还不准）| Nano Banana 2 实测不达标 | $75 |
| **D** ComfyUI 本地（最强但要 GPU）| 长期月费 > $100 时 | 一次性 ¥7500 |

---

Sources:
- [Nano Banana 2 Official Blog](https://blog.google/innovation-and-ai/technology/ai/nano-banana-2/)
- [Nano Banana 2 Pricing 2026](https://www.aifreeapi.com/en/posts/nano-banana-2-pricing)
- [Nano Banana Models Comparison](https://blog.laozhang.ai/en/posts/gemini-image-model-comparison)
- [n8n Workflow · Nano Banana Product Photos](https://n8n.io/workflows/9712-generate-ai-product-photos-using-gemini-nano-banana-with-jotform-and-google-sheets/)
- [Nano Banana Prompting Guide](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-nano-banana)
- [Nano Banana 2 Multi-Image Guide](https://createvision.ai/guides/nano-banana-2-complete-guide)
