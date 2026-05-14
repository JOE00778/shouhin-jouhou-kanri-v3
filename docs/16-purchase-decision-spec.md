# 自動発注（多仕入先決定版）· 設計仕様

> ステータス：v4 · 2026-05-14（Boss 「订货逻辑.md」公式に全面移行）
> v3 (2026-05-12) → v4 (2026-05-14) 数量公式刷新
> 目的：「どの商品を・どの仕入先から・何個」を自動決定し、**毎月定期で NetSuite 発注 CSV を自動出力**する。
> データソース：`仕入先管理リスト (1).xlsx`（29 仕入先 sheet → `supplier_quote`）+ `shop_sales`（source=export_item = 【輸出】アイテム別売上（概要））+ `item_inventory_snapshot_v2`（【輸出】在庫のスナップショット）+ `item_v2`
> 実装：`shared/purchase_engine.py` + `pages/25_📦_発注AI_v2.py`

## Boss 決定（2026-05-14 全面刷新）— 「订货逻辑.md」

数量公式（旧 `target = base × tfac × order_months` を全面置換）:

```
推奨月販     = max(平均月販, 直近月販) × トレンド係数(1.2/1.0/0.7)
実質在庫     = JD-物流-千葉 手持 + 注文済(全倉横断)        ← 弁天は含めない
上次発注時剩余 = 実質在庫 − 推奨月販
目標在庫     = 推奨月販 × 1.5
必要数       = 目標在庫 − 上次発注時剩余 = 推奨月販 × 2.5 − 実質在庫
発注箱数     = CEIL(必要数 / ケース入数)                  ← ロット ではなく ケース
発注数       = 発注箱数 × ケース入数
```

- トレンド窓を 3 ヶ月 → **4 ヶ月** に拡大（Boss 2026-05-14）
- トレンド係数 1.2/1.0/0.7 は保持
- 取整は **ケース入数 (case_qty)** が優先, 無ければ lot_size にフォールバック (`pack_source` 列に表示)
- 弁天倉庫の手持は実質在庫から除外（Boss 2026-05-14）
- 確保済 (qty_committed) は引かない（Boss 仕様）

状態 (`status`):
- `recommended` = 発注対象
- `needs_review` = 出はするが人工確認 (`ケース÷推奨月販 ≥ 2.5` または ケース/ロット未設定)
- `new_passive` = NEW ランク = 受動発注 (需要が来てから手動。発注数 0 で list 出力)
- `deferred_overstock` = `max_stock_months` 上限超
- `deferred_min_order` = 仕入先小計が最低受注額未達

## Boss 決定（2026-05-12 追加・上書き）

- **弁天経由の中継費 = 撤廃**。弁天は自社倉庫で費用が発生しない → `ZONE_MARKUP` 全 1.00、運費モデルも当面なし（他社の運費・送料無料閾値も今は考慮しない）。
- **在途 = 在庫スナップショットの「注文済」列**（発注済で未入荷）。「輸送中」列は無視。`発注残` は別途考慮しない。
- **発注数 = max(0, 目標在庫 − 有効在庫) を ロット倍数に切り上げ**
  - `有効在庫 = 手持 − 確保済 + 注文済`
  - `目標在庫 = max(平均月販, 直近月販) × トレンド係数(1.2/1.0/0.7) × (納期カバー月数 + 安全在庫月数)`
  - 不足 ≤ 0 の SKU は推奨に出さない
- **取扱中止 / メーカー取扱中止 の SKU は発注対象外**（`item_v2.handling_status` / `rank` で判定）。UI に除外件数を表示。
- **ロット起定量**: 仕入先管理リストの各仕入先の `lot_size` の倍数で発注（切り上げ）。**ただし ハリマ / SD（応急/参考用）はロット無視 = 必要数ぴったり発注**（`NO_LOT_SUPPLIERS`, Boss 2026-05-12）。→ 応急で買う遅売れ SKU が起定量で積み上がるのを防ぐ。
- **予算上限・キャッシュフロー制約 = 当面なし**（モデル落地優先）。**最低受注額**は `仕入先管理リスト` に値があるものだけ判定（無いものは制約なし）。
- 2026年2月の売上 = 異常のため無視（穴があってもそのまま）。**季節性商品なし**。
- **仕入先選定の枠組み**:
  1. 各 SKU はまず候補仕入先を **zone優先（JD直送 > 弁天 > 応急 > 前払い）→ 同 zone は単価最安** でランク。1位=主力、2〜3位=備用① / 備用②（UI・出力に並記）。
  2. さらに **メーカー（品牌）単位で 1〜3 仕入先に集約**。あるメーカーの SKU を最も多くカバーできる仕入先をアンカーに（JD直送→弁天 の順、最大 3 社）。各 SKU はその SKU の最良 zone と**同じ zone** のアンカーへ寄せる。
     - ⚠️ **zone tier をまたいだ移動はしない**（JD直送 の SKU を 応急/前払い へ移すことは絶対にない）。最良 zone が 応急/前払い の SKU はそのまま。
     - 元の最安より **1.5 倍超** に高くなる移動はしない（変な集約を防ぐガード）。
     - **メーカー全体の SKU 数が小さい場合（既定 ≤5）は集約しない**（「品牌が数 SKU しか無い場合は無視」ルール）。
  3. パラメータ（page 25 UI で調整可）: `consolidate_by_brand` / `max_suppliers_per_brand`(既定3) / `small_brand_skip`(既定5)。
  → 実装: `shared/purchase_engine.py` の `_consolidate_brand()`。出力 DataFrame に `supplier_primary` / `supplier_backup1` / `supplier_backup2`（+各単価）/ `consolidated`(集約で主力が変わったか) 列。
  4. **発注先の選び方 = `optimize` パラメータ**（page 25 にラジオ）:
     - `'zone'`（既定）= zone優先 → 同zone最安。会社の zone 戦略（応急は兜底）を守る。
     - `'line_cost'` = **発注金額(line_cost)が最小になる仕入先**を選ぶ（ロット丸め・納期込み）。= 「最小支出プラン」。
       ⚠️ zone をまたいで安い方へ動くので JD直送 → 弁天/応急/前払い への移動が起きる。
     - `'cost'` = 純粋に最安単価（ロット無視）。比較用。
     - 試算（2026-05-12, ローカル）: 現行(zone優先+品牌≤3集約) ¥21.3M / `line_cost`最小(集約なし) **¥19.3M(−9.3%)** / `line_cost`最小+品牌≤3集約 ¥20.8M(−2.5%)。
       最低受注額制約は現データではどのシナリオでも非拘束（NEW WIND ¥50k / カネイシ ¥10k / HK ¥48 のみ設定, いずれも余裕で達成）。
  5. **ランク絞り込み** = `ranks` パラメータ（page 25 にマルチセレクト, 既定 ('Aランク','Bランク')）。指定すると `item_v2.rank` がそのリストの SKU のみ対象。「まず A/B 等級の货を見る」用。
  6. **在庫月数の上限** = `max_stock_months` パラメータ（page 25 にチェックボックス + 数値, 既定オフ）。
     ロット起定量で「発注後の在庫月数 = (有効在庫 + 発注数) / 月販」が上限を超える SKU を `status='deferred_overstock'` にして発注対象から外す（買い過ぎ防止 = 在庫回転の健全性ガード）。
     試算（A/B のみ）: 上限なし 111 SKU ¥9.3M / 6ヶ月 102 SKU ¥8.8M(保留9件) / 4ヶ月 89 SKU ¥7.1M(保留22件) / 3ヶ月 69 SKU ¥5.5M(保留42件)。
     ※「1ヶ月分が起定量に満たない場合 1.5〜2ヶ月分まで OK、ただし在庫回転が健全範囲内」(Boss 2026-05-12) の実装。order_months 自体は納期ベース(≈2)のまま、上限はその上の安全弁。

## Boss 決定（2026-05-11）

1. **「納品可能日 記入欄 → 仕入先返送」フロー = 削除**。商談で月次固定化されるので、毎月定期に NST 発注 CSV を自動出力するだけ。→ **P4 廃止**
2. ~~B 区分（共和・大木）の中継費 = +3%~~ → **2026-05-12 撤廃**（上記）
3. **発注月数** = 納期（リードタイム）から自動補正（`order_months = ceil(lead_time_days / 30) + 安全在庫1ヶ月` 等）
4. **トレンド係数** = 1.2 / 1.0 / 0.7 で OK。**結果にトレンド値（↑↓→ と倍率）を明示**
5. **発注書フォーマット** = `pages/10_📦_発注書作成.py` の NST 発注 CSV と同形式（13 列）
6. **最低月販フィルタ = なし**。ランク区別なし、**全 SKU を計算対象**にして方案を出す（販売 0 の SKU は自然に発注数 0 で除外）

---

## 1. 仕入先の分類（Boss 2026-05-11 決定）

| 区分 | 仕入先 | 配送ルート | 優先度 |
|---|---|---|---|
| **A. JD 直送**（追加配送費なし） | NEW WIND / 中央物産 / 菅野 / Maple / 五洲 / アプライド / 王子国際 / ハナモン / HK / オンダ / スケーター / ファイン / 新日配 / グランジェ / トラスコ / エンパイヤ | → JD-物流-千葉 直送 | 🥇 最優先 |
| **B. 弁天経由**（中継配送費が発生） | 共和 / 大木 | → 弁天倉庫 → JD-物流-千葉 | 🥈 次点（中継費を含めて比価） |
| **C. 応急用**（参考価格・カード仕入） | SD(参考) / ハリマ(参考) / カード仕入 | — | 🥉 新商品 / 他に仕入先がない時のみ |
| **D. 前払い**（現金） | 流久（現金）/ 富森（現金）/ 風雲商事（現金） | — | ⛔ なるべく使わない（最後の手段） |

**比価ルール**：
- A 区分内で `単価` 最安を選ぶ
- A に該当なし → B（`実質単価 = 単価 + 中継費按分` で比較）
- B にも該当なし → C
- それでもなし → D
- ⚠️ 同区分内で価格が拮抗する場合は `納期が短い` 方を優先

> ❓ **要確認 #1**：B 区分の中継配送費は固定額か、出荷量に比例か。比例なら「1 個あたり ◯ 円」の係数が必要。

---

## 2. 基础订货数（発注数量）の算定

各 SKU について：

```
1. shop_sales から直近 3 ヶ月の販売数量を取得
   月別: m1, m2, m3 (m3 = 直近月)

2. 平均月販 avg = (m1 + m2 + m3) / 3

3. トレンド判定:
   - 上昇: m3 > m2 > m1   かつ  m3 ≥ avg × 1.2
   - 下降: m3 < m2 < m1   かつ  m3 ≤ avg × 0.8
   - 横ばい: 上記以外

4. 基础订货数 base_qty:
   base_qty = avg × order_months × trend_factor
     - order_months = AB商品进货周期 sheet の「建议每次订货/月」(デフォルト 1)
     - trend_factor = 上昇 1.2 / 横ばい 1.0 / 下降 0.7

5. ロット丸め:
   final_qty = ceil(base_qty / lot_size) × lot_size
     - lot_size = 選択した仕入先の「ロット」
     - final_qty が 0 になる場合（avg が極小）は発注対象外
```

> ❓ **要確認 #2**：`order_months`（何ヶ月分まとめて発注するか）— AB商品进货周期 sheet の「建议每次订货/月」をそのまま使うか、納期（リードタイム）を加味して自動算出するか。
> ❓ **要確認 #3**：トレンド係数（1.2 / 1.0 / 0.7）は初期値。実績で調整。

---

## 3. 4 維フィルタ（仕入先選定）

各 SKU の発注候補仕入先について、以下の順で篩い分け：

| # | フィルタ | 条件 | 不合格時 |
|---|---|---|---|
| ① | **基础起订量**（ロット） | `final_qty ≥ lot_size` | ロット未満なら次回に持ち越し or 別仕入先 |
| ② | **品牌起订量**（注文最低金額） | その仕入先への**合計発注額** ≥ `注文最低金額` | 他 SKU と合算しても届かなければ発注見送り（次月へ） |
| ③ | **運費起订量** | ②と同じ閾値（`注文最低金額` が送料無料ライン or 最低受注額を兼ねる） | 同上 |
| ④ | **価格優先** | ①②③を満たす仕入先の中で `単価`（B 区分は実質単価）最安 | — |

**重要**：②③は**仕入先単位の合算判定**。SKU 単位ではなく、その仕入先に発注する全 SKU の `Σ(final_qty × 単価)` で判定する。

**アルゴリズム（擬似コード）**：
```python
# Step 1: 各 SKU の暫定割り当て（区分優先 → 価格最安）
for sku in skus_with_demand:
    quotes = supplier_quotes[sku]                # [(supplier, unit_price, lot, min_amount), ...]
    quotes = sort_by(quotes, key=(zone_rank(supplier), effective_price))
    for q in quotes:
        if final_qty(sku, q.lot) >= q.lot:       # ① 基础起订量
            tentative[sku] = q
            break

# Step 2: 仕入先単位で合算 → ②③ 注文最低金額チェック
for supplier, items in group_by_supplier(tentative):
    total = sum(final_qty(it) * it.unit_price for it in items)
    if total < supplier.min_order_amount:        # ②③ 未達
        # 救済1: ランク高い SKU を多めに（買い込み）
        # 救済2: それでも未達なら supplier の割り当てを全部解除 → 次優先の仕入先へ再割当
        reassign_or_defer(items)

# Step 3: B 区分は中継費を加味して A 区分と再比較（②達成後）
```

---

## 4. 「納品日確認」のラウンドトリップ

発注書には**仕入先に記入してもらう欄**を含める：

| 列 | 値 | 記入者 |
|---|---|---|
| JANコード | … | 自社（自動） |
| 商品名 | … | 自社 |
| 発注数量 | `final_qty` | 自社 |
| 単価 | … | 自社 |
| 希望納品先 | "JD-物流-千葉"（A区分）/ "弁天倉庫"（B区分） | 自社 |
| **納品可能日** | （空欄）← 仕入先が「今月◯日」と記入 | **仕入先** |
| **納品可能数量** | （空欄）← 欠品時に仕入先が記入 | **仕入先** |
| 備考 | … | 仕入先 |

フロー：
```
1. システム → 発注書 Excel/CSV 生成（仕入先ごとに 1 ファイル）
2. 担当者 → 各仕入先へ送付（メール等）
3. 仕入先 → 「納品可能日」「納品可能数量」を記入して返送
4. 担当者 → 返送 Excel をシステムにアップロード
5. システム → 確定発注 = NetSuite 注文書 CSV 出力（page 24 と同じ NST フォーマット）
   ※ 弁天経由（B区分）の場合は配送伝票 CSV も追加生成
```

---

## 5. データモデル（新規テーブル）

### `suppliers`（仕入先マスタ）
```sql
CREATE TABLE suppliers (
  supplier_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  name             TEXT NOT NULL UNIQUE,        -- 'NEW WIND' / '中央物産' / ...
  zone             TEXT NOT NULL,               -- 'JD_DIRECT' / 'BENTEN_TRANSIT' / 'EMERGENCY' / 'PREPAID'
  zone_rank        INTEGER NOT NULL,            -- 1=A / 2=B / 3=C / 4=D（ソート用）
  min_order_amount INTEGER,                     -- 注文最低金額（円, NULL=制限なし）
  transit_fee_rule TEXT,                        -- B区分のみ: 中継費の算定ルール
  default_lead_days INTEGER,                    -- 標準納期（日数）
  notes            TEXT,
  updated_at       TEXT NOT NULL
);
```

### `supplier_quotes`（仕入先 × SKU 報価）
```sql
CREATE TABLE supplier_quotes (
  supplier_id   INTEGER NOT NULL REFERENCES suppliers(supplier_id),
  jan           TEXT NOT NULL,
  display_name  TEXT,
  unit_price    INTEGER NOT NULL,               -- 単価（円）
  lot_size      INTEGER NOT NULL DEFAULT 1,     -- ロット（最小発注単位の数量）
  case_qty      INTEGER,                        -- 入数（ハリマ等のみ）
  order_condition TEXT,                          -- 発注条件「ケース単位」「ロット単位発注」等
  lead_time_text TEXT,                           -- 納期「2週間」等（生テキスト）
  source_sheet  TEXT,                            -- 元 sheet 名
  imported_at   TEXT NOT NULL,
  PRIMARY KEY (supplier_id, jan)
);
CREATE INDEX idx_sq_jan ON supplier_quotes(jan);
```

### `purchase_recommendation`（発注推奨 = 本モジュールの出力）
```sql
CREATE TABLE purchase_recommendation (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at            TEXT NOT NULL,               -- 計算実行日時
  jan               TEXT NOT NULL,
  display_name      TEXT,
  rank              TEXT,
  avg_monthly_sales REAL,                        -- 直近3ヶ月平均月販
  trend             TEXT,                        -- 'up' / 'flat' / 'down'
  suggested_qty     INTEGER,                     -- ロット丸め後の発注数量
  supplier_id       INTEGER REFERENCES suppliers(supplier_id),
  supplier_name     TEXT,
  zone              TEXT,
  unit_price        INTEGER,
  lot_size          INTEGER,
  line_amount       INTEGER,                     -- suggested_qty × unit_price
  supplier_total    INTEGER,                     -- その仕入先の合計発注額（min_order_amount 判定済）
  meets_min_order   INTEGER,                     -- 0/1
  status            TEXT,                        -- 'recommended' / 'deferred_min_order' / 'no_supplier'
  reason            TEXT,                        -- 選定理由（人間が読める）
  imported_at       TEXT NOT NULL
);
CREATE INDEX idx_pr_run ON purchase_recommendation(run_at);
CREATE INDEX idx_pr_supplier ON purchase_recommendation(supplier_id);
```

---

## 6. UI（新規 page 25「📦 発注AI v2」）

```
┌──────────────────────────────────────────────────────────────┐
│ ① 仕入先管理リスト.xlsx をアップロード                          │
│    → 28 仕入先 sheet を解析 → suppliers + supplier_quotes 更新 │
│    （or 既存データを使う）                                       │
├──────────────────────────────────────────────────────────────┤
│ ② パラメータ                                                   │
│    - 対象期間: 直近 [3] ヶ月                                    │
│    - 最低月販フィルタ: [10] 個以上                              │
│    - 発注月数: AB进货周期に従う / 一律 [1] ヶ月                  │
│    - トレンド係数: 上昇[1.2] 横ばい[1.0] 下降[0.7]              │
│    [🔍 発注計算実行]                                            │
├──────────────────────────────────────────────────────────────┤
│ ③ 結果（仕入先ごとにグループ表示）                              │
│    📦 NEW WIND（JD直送） — 合計 ¥XXX,XXX ✅最低受注額クリア    │
│       JAN | 商品名 | 月販 | trend | 発注数 | 単価 | 金額       │
│       ...                                                      │
│    📦 共和（弁天経由・中継費込み）— 合計 ¥XX,XXX               │
│       ...                                                      │
│    ⚠️ 発注見送り（最低受注額未達）: N 件                         │
│    ❌ 仕入先なし: N 件                                          │
│    [⬇️ 発注書 Excel（仕入先別）] [⬇️ 全件 CSV]                  │
├──────────────────────────────────────────────────────────────┤
│ ④ 納品日記入済み Excel を再アップロード                         │
│    → 確定発注 → NetSuite 注文書 CSV（+ B区分は配送伝票 CSV）   │
└──────────────────────────────────────────────────────────────┘
```

既存 `pages/08_📦_発注AI.py` は単純版（単一ロジック）なので、本モジュールは **page 25 新規**として作る。08 は後で統合 or 廃止判断。

---

## 7. 落地ステップ

| Phase | 内容 | 依存 |
|---|---|---|
| **P1** | DDL（3 テーブル）+ `supplier_quotes` ingester（28 sheet 解析、zone 自動付与） | — |
| **P2** | 発注計算エンジン: 3ヶ月トレンド + ロット丸め + 4維フィルタ + 仕入先合算 + zone 優先 | P1 + shop_sales |
| **P3** | page 25 UI（上記モック）+ 発注書 Excel 出力（納品日記入欄付き） | P2 |
| **P4** | 納品日返送 Excel の取込 → NST 注文書/配送伝票 CSV 出力（page 24 連携） | P3 + page 24 |

---

## ❓ Boss 確認待ち

1. **B 区分（共和・大木）の中継配送費**：固定額？ 出荷量比例？ 係数は？
2. **発注月数**：AB进货周期 sheet の「建议每次订货/月」をそのまま使う？ 納期で自動補正する？
3. **トレンド係数 1.2/1.0/0.7**：初期値で OK？
4. **発注書フォーマット**：仕入先に送る Excel のレイアウト指定はある？（既存の発注書テンプレがあれば共有を）
5. **最低月販フィルタ**：`仕入れ情報.xlsx` は「月販 ≥ 10」だが、A/B/C ランク別に閾値を変える？（A は全件、C は ≥10 等）
