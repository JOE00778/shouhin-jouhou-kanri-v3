# ComfyUI Windows 部署调研（与 N8N 出图方案并行评估）

> 状态：调研草案 · 2026-05-08
> 目的：评估在 Boss 现有 Windows 笔记本（Inspiron 5405）部署 ComfyUI
>      做主图 + 详情图的可行性，并与 N8N 路线并行权衡

---

## 1. 硬件门槛 · ⚠️ 高风险

ComfyUI 跑 Stable Diffusion 类模型，**有 NVIDIA GPU 是常态、AMD GPU 勉强、纯 CPU 慢到不可用**。

| 配置 | SDXL 单图 1024² 出图耗时 | 评级 |
|---|---|---|
| RTX 4090 (24 GB) | ~3-5 秒 | 🟢 理想 |
| RTX 3060 (12 GB) | ~15-25 秒 | 🟢 主流够用 |
| RTX 4060 (8 GB) | ~25-40 秒 | 🟡 卡量化模型 |
| **Inspiron 5405 集成显卡 (Vega 6)** | **不支持** | 🔴 |
| **Inspiron 5405 CPU only** | **8-15 分钟/张** | 🔴 不可用 |

**Inspiron 5405** = Ryzen 5 4500U + Vega 6 集显 + 16 GB RAM。集显 Vega 6 不在 Pytorch
ROCm 支持列表里，CPU 跑 SDXL 一张要 10 分钟以上 — **不能做生产用途**。

**结论：现有硬件跑 ComfyUI 不可行。要走 ComfyUI 路线 = 必须采购 GPU。**

---

## 2. 三种实施路径

### 路径 A · 本机 ComfyUI（需采购 GPU）

| 项 | 估算 |
|---|---|
| GPU | RTX 4060 Ti 16GB（含税）¥3,800 |
| 平台 | 现有 Inspiron 不能加显卡 → 需要新主机 |
| 主机 | 二手台式 Ryzen 5 5600 + B450 + 32GB ¥3,500 |
| 月度电费 | ~¥30（按每天出 50 张图、闲时 idle） |
| **一次性** | **~¥7,500** |
| **月费** | **~¥30** |

体验：本机出图最快、隐私最强、可装任意自定义模型 / LoRA / ControlNet。

### 路径 B · 云端 ComfyUI（按需付费）

| 服务商 | 价格 | 备注 |
|---|---|---|
| RunPod ComfyUI Pod | ~$0.30/小时（RTX A4000） | 按秒计费，闲置可停 |
| Vast.ai | ~$0.20-0.50/小时 | 议价市场，价格不稳 |
| ThinkDiffusion | $0.5/小时（A10G） | 一键启动，UI 友好 |
| **预估月费** | **$30-60（按每天 1 小时活跃）** | |

体验：随用随开，API 调用从 N8N 也能直连（HTTP 节点），灵活度最高。

### 路径 C · 第三方 SaaS API（最简）

直接调商业出图 API，**ComfyUI / N8N 都做 client**：

| API | 单图价格 | 优势 |
|---|---|---|
| OpenAI DALL-E 3 / gpt-image-1 | ~$0.04-0.12 | 已有账号 |
| Anthropic（暂无图像生成） | — | — |
| Stability AI SD3 API | ~$0.04 | 商业模型 |
| Recraft V3 | $0.04 | 商品图最准 |
| BFL FLUX 1.1 Pro | $0.04 | 写实最强 |

按每天 50 张算：**月费 ~$60-180**。无需硬件，N8N 直接用 HTTP 节点就能调。

---

## 3. ComfyUI vs N8N 出图能力对比

| 维度 | ComfyUI | N8N（用商业 API）|
|---|---|---|
| 节点式编排 | ✅ 原生 | ✅ HTTP 节点 |
| 自定义模型 / LoRA | ✅ 强项 | ❌ 取决于 API |
| ControlNet / IP-Adapter（参考图复用风格）| ✅ 原生支持 | 🟡 部分 API 提供 |
| 7×24 自动批量出图 | 🟡 需脚本/API server | ✅ N8N 原生 cron |
| 商品详情图（多张拼图、版式） | 🟡 出图后还需后期 | 🟡 同 |
| 一致性（按参考图风格出多张商品图） | ✅ 用 ControlNet/IPA 可复用 | 🟡 依赖 API 是否支持 reference image |
| 学习成本 | 🔴 中高（节点式 + 模型管理） | 🟢 低（HTTP 调 API） |
| **风格还原 Boss 提供的参考图** | **🟢 ControlNet + IP-Adapter 业界最强** | **🟡 依赖 API 商家** |

**关键差异**：Boss 说"按参考图做详情图，其他商品的详情图" — 这是**风格迁移 + 一致性**任务。
ComfyUI 的 ControlNet + IP-Adapter 组合是业界出图复刻参考图风格的最强工具，
而 SaaS API 在风格一致性上能力弱（输入参考图后输出与原图相似度低）。

如果 Boss 对"详情图风格统一"要求高 → ComfyUI 必选。
如果"差不多就行" → N8N 调 SaaS API 月费几十刀就够。

---

## 4. Windows ComfyUI 部署方案（如果选定）

### 4-A · 官方便携版（推荐做安装包）

```
ComfyUI_windows_portable_nvidia.7z
└── 解压 → 双击 run_nvidia_gpu.bat
```

优点：零依赖（自带 Python embedded），双击即跑
缺点：仅 NVIDIA；需要 7-Zip 解压

### 4-B · ComfyUI Desktop App（2025 后官方）

`https://www.comfy.org/download` → 下载 `.exe` 双击安装。
体验最接近"普通桌面软件"，自动检测 GPU + 装驱动。

### 4-C · 装到 Docker Compose（与 N8N 同栈）

可以做。但 Windows Docker Desktop 默认不直通 GPU（要配置 NVIDIA Container Toolkit），
对 Boss 略复杂。**建议如果走 ComfyUI 不要走 Docker，直接用便携版**。

### 安装包形态（如果 Boss 决定走路径 A）

类比当前 N8N 安装包，可以做 `Smikie-ComfyUI-Installer-v1.0.zip`：

```
Smikie-ComfyUI/
├── installer/
│   ├── install.bat                    ← 双击入口
│   ├── check-gpu.ps1                  ← NVIDIA GPU 检测（无则提示买）
│   ├── download-comfyui.ps1           ← 下载 portable .7z + 解压
│   ├── download-models.ps1            ← 下载基础模型（SDXL ~7 GB）
│   └── install-models.ps1             ← 内置参考图风格的 IP-Adapter / ControlNet
├── workflows/                         ← ComfyUI 工作流 JSON
│   ├── product-main-image.json        ← 主图 workflow（白底 + 商品居中）
│   └── product-detail-image.json      ← 详情图 workflow（参考图复刻）
├── reference-images/                  ← Boss 放参考图
└── output/                            ← 生成图保存
```

但 ComfyUI 模型文件巨大（SDXL base 7 GB / IP-Adapter 3 GB），不能内置进 zip，
要做"安装时在线下载"逻辑。

---

## 5. 推荐路径（决策树）

```
Boss 对详情图"风格一致性"要求？
  ├─ 极高（详情图必须复刻参考图味道）
  │    → 路径 A：买 GPU 主机（¥7500）+ ComfyUI 便携版
  │      推荐 RTX 4060 Ti 16GB / Ryzen 5 5600
  │      做成第二个 Smikie 安装包
  │
  ├─ 中等（差不多就行，可用商业 API 凑）
  │    → 路径 C：N8N 直接调 SaaS API（gpt-image-1 / FLUX）
  │      零硬件投入，月费 $60-180
  │      在现有 N8N 安装包加 image-generation workflow 即可
  │
  └─ 还没定/想先试试
       → 路径 B：RunPod 按小时租 ComfyUI Pod
         按需起，停了不收钱，先试 1-2 周
         决定后再决定 A 或 C
```

---

## 6. 下一步建议（Boss 拍板用）

| 选项 | 适用情况 | 我能做的 |
|---|---|---|
| **A** | Boss 想要最强风格一致性 + 长期省钱 | 写 ComfyUI 安装包；同时 N8N 加 ComfyUI workflow（HTTP 调本地 8188 端口）|
| **C** | Boss 想立即出图、不想买硬件 | N8N 加 image-generation workflow（FLUX/gpt-image-1 二选一），1 天能跑出第一批样图 |
| **B** | 还想先试 | 出 RunPod 操作指引文档，Boss 试 1 小时 = $0.30 |
| **A+C 并行** | 预算足、想全面对比 | 我先做 N8N + SaaS API（快出样），再做 ComfyUI 安装包（精出样），两套都试用 1 周决定 |

> Boss 之前说"两套方案可以并行" → 默认走 **A+C 并行**，但 A 的硬件采购需要 Boss 先决定。
> 在硬件到位前先做 C（N8N + SaaS API），让出图链路立刻能跑。

---

## 7. 待 Boss 拍板的项

1. **路径选择**（A / B / C / A+C 并行）
2. **预算上限**（决定能否走 A）
3. **参考图准备**（请把"想要复刻风格的详情图"拷给我 3-5 组）
4. **目标输出量**（每天多少张？影响成本估算和硬件等级选型）
5. **优先级**（详情图 vs 主图哪个先做）
