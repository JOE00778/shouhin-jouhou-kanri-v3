# N8N 自动作图 · 工具生态扩展调研

> 状态：调研草案 · 2026-05-08
> 上游文件：[05-comfyui-research.md](05-comfyui-research.md)（聚焦 ComfyUI 本地路线）
> 本文范围：除 ComfyUI 外，**所有可被 N8N 直接 HTTP 调用** 的出图工具
> 目标：Boss "按参考图做详情图，其他商品的详情图" + 主图 + 场景图

---

## 1. 任务拆解（决定选什么工具）

商品图自动化不是"出一张图"，而是**多步流水线**：

```
输入: 原始商品照片（白底 / 拍摄）+ 文案 + 风格参考图
  │
  ├─[A]─ 抠图 / 白底 / 阴影标准化   →  主图
  │
  ├─[B]─ AI 生场景 / 投放到生活场景 →  场景图（一商品出多场景）
  │
  ├─[C]─ 风格迁移（按参考图复刻）   →  风格统一的详情图
  │
  └─[D]─ 模板化排版（标题+商品图+卖点+装饰）→  详情图终稿
```

**没有任何一个工具能一锅端做完 A+B+C+D**，必须组合。所以选型要按"任务 × 工具能力"做。

---

## 2. 任务 → 工具能力矩阵

| 任务 | 第一选择 | 备选 | 备注 |
|---|---|---|---|
| **A. 抠图 + 白底主图** | **Photoroom API** | Remove.bg / ClipDrop / Cutout.pro | Photoroom 在电商场景压倒性领先 |
| **B. AI 生场景图（"放进咖啡馆里拍"）** | **Photoroom Generate Background** | Pixelcut.ai / Booth AI | Photoroom 自带"商品场景"专用模式 |
| **C. 风格复刻（按参考图同款风格）** | **fal.ai 跑 FLUX+IP-Adapter** | Replicate IP-Adapter / 自建 ComfyUI | 不用自建 GPU，按张计费 |
| **D. 模板化详情图（含文字+排版）** | **Bannerbear** | Placid / APITemplate / Templated | Bannerbear N8N 集成最成熟 |
| 文字渲染（详情图带日文卖点）| Ideogram 3 | Recraft V3 / Imagen 4 | 日文/中文文字渲染好 |
| 整图重画（无原图，纯生成）| Recraft V3 | FLUX 1.1 Pro / Imagen 4 | 商品图"想象出来"用 |

---

## 3. 三个落地方案（任选一套，或组合）

### 🟢 方案 X · 纯 SaaS 组合（推荐起步，本周可跑）

```
N8N workflow
   │
   ├─ 拉商品原图（Google Drive / CMS）
   ├─ POST Photoroom /v1/segment       → 抠图
   ├─ POST Photoroom /v1/generate-bg   → 多场景投放（一商品出 5 张）
   ├─ POST Bannerbear /v2/images       → 套模板（标题、卖点、商品图槽位）
   └─ 上传 Shopee / Shopify / 飞书
```

**月度成本估算（每天 50 张商品图 × 5 场景 = 250 张/天）**：

| 项 | 单价 | 月用量 | 月费 |
|---|---|---|---|
| Photoroom API（抠图 + 场景）| $0.05/张 | 250×30=7,500 | **$375** |
| Bannerbear（详情图模板）| $49/月 1k credits | 用 7,500 credits 升 Pro | **$99** |
| **小计** | | | **$474** |

但每天 50 个新品太激进。按"50 张图/天"（不是 50 个商品 × 5）：

| 现实量 | Photoroom | Bannerbear | 合计 |
|---|---|---|---|
| 50 张/天 = 1,500/月 | $75 | $49 | **$124** |
| 20 张/天 = 600/月 | $30 | $15（Placid）| **$45** |

**优势**：零硬件、零 ComfyUI 学习成本、N8N 原生 HTTP 节点、3 天跑起来。
**劣势**：风格复刻能力有限（Photoroom 场景库有限，不能"按 Boss 参考图味道"严格复刻）。

### 🟡 方案 Y · fal.ai 加风格复刻（中等成本，专业级风格）

```
N8N workflow
   │
   ├─ 抠图：Photoroom（同方案 X）
   ├─ 风格复刻：fal.ai/flux-pro/v1.1/redux 或 ip-adapter
   │   payload: { image_url: 商品白底, ref_image_url: Boss参考图 }
   ├─ 详情图模板：Bannerbear（同方案 X）
   └─ 上传
```

**fal.ai 价格**（2026 行情）：

| 模型 | 单价 | 用途 |
|---|---|---|
| FLUX Schnell | $0.003/张 | 草图、批量预览 |
| FLUX Dev | $0.025/张 | 主力出图 |
| FLUX 1.1 Pro | $0.045/张 | 高质量 |
| FLUX Pro Ultra | $0.06/张 | 最高质量 |
| FLUX + IP-Adapter (Redux) | $0.05/张 | **按参考图风格复刻** ⭐ |
| Recraft V3 | $0.04/张（raster）| 商品图最强 |
| Ideogram 3 Turbo | $0.0375/张 | 文字渲染（详情图带文字标语）|

**月度成本**（50 张/天 × 30 = 1,500 张）：

| 项 | 单价 | 月用量 | 月费 |
|---|---|---|---|
| Photoroom 抠图 | $0.025/张 | 1,500 | $38 |
| fal.ai FLUX+IP-Adapter | $0.05/张 | 1,500 | $75 |
| Bannerbear 模板 | — | — | $49 |
| **小计** | | | **$162** |

**优势**：风格复刻能力强（IP-Adapter 是业界标杆），按张计费可控，N8N HTTP 直连。
**劣势**：比方案 X 贵 30%，但出图质量明显更好。

### 🔴 方案 Z · 本地 ComfyUI（详见 [05-comfyui-research.md](05-comfyui-research.md)）

需采购 GPU 主机 ¥7,500，长期最便宜，风格复刻最强。

---

## 4. 推荐落地路径（决策树）

```
现在就要出图（本周内）？
  │
  ├─ Yes → 方案 X 或 Y 起步
  │     │
  │     ├─ 风格复刻要求高 → 方案 Y（fal.ai + IP-Adapter）
  │     └─ 先看质量再说  → 方案 X（Photoroom + Bannerbear）
  │
  └─ No  → 走方案 Z（本地 ComfyUI），等 GPU 到位后部署

每月出图量 > 3000 张？
  → 长期算 ComfyUI 最便宜（GPU 一次性，电费忽略）
  → 短期方案 Y 仍最快
```

**Boss 之前说"两套方案可以并行"** → 推荐 **方案 X / Y 立刻跑 + 方案 Z 同步采购** 双线推进。

---

## 5. N8N 现成 workflow 模板（可直接抄）

n8n.io workflows 仓库有大量电商出图模板，**复制后改 API key 即可跑**：

| 模板 | 链接 | 用途 |
|---|---|---|
| Automated AI Product Photography (Segmind) | n8n.io/workflows/3633 | 商品图 + Instagram 帖子 |
| Make every product photo look like luxury ad | n8n.io/workflows/6151 | 高端化包装（GDrive 触发）|
| Generate custom AI images with GPT-Image-1 | n8n.io/workflows/3705 | OpenAI 路线 |
| Flux AI image generator | n8n.io/workflows/2417 | FLUX 路线 |
| Free AI image generator (Gemini/ChatGPT) | n8n.io/workflows/5626 | 免费起步 |

**操作**：N8N UI → Workflows → Import from URL 即可。然后改 HTTP 节点 URL +
凭证 = 跑起来。

---

## 6. 我建议的 N8N workflow 蓝图（落地到现有 N8N 安装包）

加 3 个新 workflow 到 [deploy/n8n/workflows/](../deploy/n8n/workflows/)：

### product-main-image.json（主图标准化）

```
Webhook 触发 (CMS page 上传商品图)
  ↓
HTTP Photoroom /v1/segment        → 抠白底
  ↓
HTTP Photoroom /v1/edit           → 居中、阴影、白底
  ↓
S3/Drive 上传                      → 拿到 URL
  ↓
CMS 回调 + 飞书通知
```

成本：每张 ~$0.025

### product-scene-image.json（场景图，按参考图复刻）

```
Webhook 触发 (商品图 + 参考图 URL)
  ↓
HTTP Photoroom /v1/segment        → 抠白底
  ↓
HTTP fal.ai/flux-pro/redux        → IP-Adapter 复刻参考图风格
  payload:
    image_url: <商品白底>
    style_image_url: <Boss 参考图>
    prompt: "product on cafe table, cinematic"
  ↓
循环: 出 3-5 个变体
  ↓
S3/Drive 上传 → CMS + 飞书
```

成本：每张 ~$0.05 + 抠图 $0.025 = $0.075

### product-detail-page.json（详情图，模板化）

```
Webhook 触发 (商品图 + 文案数据)
  ↓
HTTP Bannerbear /v2/images
  payload:
    template: "smikie-detail-template-1"
    modifications: [
      { name: "title", text: "新作 · 防漏水筒" },
      { name: "feature_1", text: "✓ 食品级硅胶" },
      { name: "feature_2", text: "✓ 一键打开" },
      { name: "product_image", image_url: <商品图> }
    ]
  ↓
拿到 PNG URL
  ↓
S3/Drive + CMS + 飞书
```

成本：每张 ~$0.05（Bannerbear 1k credits = $49）

### 三段串成的「全自动详情图」cron workflow

```
每 4 小时 cron
  ↓
拉 CMS 待生图队列（automation_runs.module='image_gen' status='pending'）
  ↓
按队列项跑 主图 → 场景图 ×5 → 详情图（套模板）
  ↓
图片回写 CMS（image_url 字段）+ 飞书通知 Boss 审稿
```

可直接在现有 [shopee-mass-upload-cron.json](../deploy/n8n/workflows/shopee-mass-upload-cron.json) 旁
新建 `image-gen-cron.json`，cron 间隔可调（高峰可改 30 分钟一次）。

---

## 7. 立刻可执行的 3 件事

1. **Boss 先注册 3 个账号**（都有免费 credits，可立刻试）：
   - Photoroom: https://www.photoroom.com/api（10 免费 credits/月）
   - fal.ai: https://fal.ai（注册送 $1，够跑 ~20 张 FLUX Pro）
   - Bannerbear: https://www.bannerbear.com（30 张免费）

2. **拷 3-5 组参考图**（详情图风格样本）→ 我看完后定 IP-Adapter 用什么参考权重

3. **Boss 决定方案**：X / Y / Z 哪个先做？我立刻写对应 workflow JSON 加到 N8N 安装包 v1.2

---

## 8. 风险 + 补丁

| 风险 | 缓解 |
|---|---|
| Photoroom 场景库味道与 Boss 参考图不符 | 改用方案 Y（fal.ai IP-Adapter）|
| fal.ai 出图慢峰值（午高峰 15s+/张）| 加 wait 节点 + retry，cron 错峰跑（凌晨）|
| Bannerbear 模板设计成本高（首次 1-2 天）| 先用 Placid（更便宜 + 模板库丰富）|
| AI 出的图侵权 / 不能商用 | Photoroom / fal.ai / Recraft / Bannerbear 都明确标注商用 OK；Midjourney 慎用（条款最严）|
| 月度账单失控 | 每个 workflow 加 monthly budget guard（N8N 可写计数器）|

---

Sources:
- [n8n.io workflows · Product Photography](https://n8n.io/workflows/3700-automated-generation-of-ai-advertising-photos-for-product-marketing/)
- [n8n.io workflows · Luxury Product Photo](https://n8n.io/workflows/6151-make-every-product-photo-look-like-a-luxury-ad-fully-automated-ai-google-drive/)
- [fal.ai vs Replicate Pricing](https://www.teamday.ai/blog/fal-ai-vs-replicate-comparison)
- [AI Image API Pricing 2026](https://www.digitalapplied.com/blog/ai-image-generation-api-pricing-comparison-2026)
- [Bannerbear API Reference](https://developers.bannerbear.com/v2/)
- [Bannerbear Alternatives 2026](https://templated.io/blog/best-bannerbear-alternatives/)
- [Photoroom Generate Background API](https://www.photoroom.com/api/generate-background)
- [Photoroom April 2026 Updates](https://www.photoroom.com/inside-photoroom/new-in-product-april-2026)
- [Best Image Gen APIs 2026](https://dev.to/iterationlayer/best-image-generation-apis-in-2026-3gm4)
- [Recraft V3 vs Ideogram for Ecommerce](https://blog.segmind.com/ideogram-3-0-on-segmind-features-api-pricing-and-use-cases/)
- [IP-Adapter Style Transfer Guide](https://stable-diffusion-art.com/ip-adapter/)
- [ComfyUI IPAdapter Plus Style Transfer](https://www.runcomfy.com/comfyui-workflows/comfyui-ipadapter-plus-style-transfer-made-easy)
