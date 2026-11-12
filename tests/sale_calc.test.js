// 运行：node tests/sale_calc.test.js
const assert = require("assert");
const s = require("../static/sale-calc.js");

const r = { ...s.SALE_DEFAULT_RULES };
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-6, `${msg}: ${a} != ${b}`);

// 按期报 P 数（平均克数）归档
assert.strictEqual(s.salePriceForGrams(250, r), 0);
assert.strictEqual(s.salePriceForGrams(280, r), 0);
assert.strictEqual(s.salePriceForGrams(350, r), 30000);
assert.strictEqual(s.salePriceForGrams(420, r), 37000);
assert.strictEqual(s.salePriceForGrams(750, r), 37000);
assert.strictEqual(s.salePriceForGrams(751, r), 35000);
assert.strictEqual(s.salePriceForGrams(1001, r), 30000);
assert.strictEqual(s.salePriceForGrams(null, r), null);

// 池：5000 kg、10000 尾 → 500 g/尾（P 2.0）→ 3.7 万/吨 → 5 吨 × 37000
let p = s.saleEstimatePond({ name: "30#", weight: 5000, count: 10000 }, r);
near(p.tons, 5, "tons"); near(p.amount, 185000, "amount");
assert.strictEqual(p.source, "期报P数"); assert.strictEqual(p.p, 2);

// 停料掉重 2%
near(s.saleEstimatePond({ name: "30#", weight: 5000, count: 10000 }, { ...r, loss: 2 }).tons, 4.9, "loss tons");

// 分段吨数：4 吨 P1.33～2.38（3.7 万）+ 1 吨 P1.0～1.33（3.5 万）+ 0.5 吨小规格（3.0 万）+ 0.2 吨扣重
const sp = s.saleFromSplit([0.2, 0.5, 4, 1, 0], r);
near(sp.tons, 5.7, "split tons");
near(sp.price, (0.5 * 30000 + 4 * 37000 + 1 * 35000) / 5.7, "split price");
near(sp.deductPct, 0.2 / 5.7 * 100, "deduct pct");
assert.strictEqual(s.saleFromSplit([0, 0, 0, 0, 0], r), null);
assert.strictEqual(s.saleFromSplit(undefined, r), null);

// 填了分段：吨数用填的（不是期报重量），金额 = 各段吨数 × 各段价格；掉重同样扣
p = s.saleEstimatePond({ name: "30#", weight: 9000, count: 30000 }, { ...r, loss: 10 }, [0, 0, 4, 1, 0]);
near(p.tons, 4.5, "split uses entered tons"); near(p.reportTons, 9, "report tons kept");
near(p.amount, (4 * 37000 + 1 * 35000) * 0.9, "split amount"); assert.strictEqual(p.source, "分段");

// 合计：两池 8 吨，订单 6 吨 → 多 2 吨，按订单量算钱
const t = s.saleEstimate([
  { name: "30#", weight: 5000, count: 10000 },   // 37000
  { name: "35#", weight: 3000, count: 3500 },    // 857 g → 35000
  { name: "36#", weight: 0, count: 0 },          // 缺数据
], { ...r, orderTons: 6 });
near(t.tons, 8, "total tons"); near(t.gap, 2, "gap");
near(t.amountAll, 185000 + 105000, "amount all");
near(t.amountOrder, 6 * 290000 / 8, "amount order");
assert.deepStrictEqual(t.missing, ["36#"]);
assert.strictEqual(t.avgP, s.saleP(8000 * 1000 / 13500));

// 分段覆盖期报
const t2 = s.saleEstimate([{ name: "30#", weight: 1000, count: 2000 }], r, { "30#": [0, 0, 0, 2, 0] });
near(t2.rows[0].price, 35000, "split overrides"); near(t2.tons, 2, "split tons in total");

// P 数换算和段落
assert.strictEqual(s.saleP(500), 2);
assert.strictEqual(s.saleP(380), 2.6);
assert.deepStrictEqual(s.saleBandPLabels(r), ["P 3.57 以上", "P 2.39～3.56", "P 1.33～2.38", "P 1.0～1.33", "P 不到 1.0"]);

console.log("sale-calc: all ok");
