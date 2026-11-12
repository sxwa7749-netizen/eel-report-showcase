"use strict";
// 卖鱼预估的计算规则（老板端「卖鱼」页用）。只做计算，不读写数据。
// 价格单位：元/吨；重量：kg（各段分吨填的是吨）；规格：克/条，页面上换算成 P 数显示。

const SALE_DEFAULT_RULES = {
  cutoff: 280,     // ≤ 这个克数：扣重（不算钱）
  smallMax: 419,   // cutoff+1 ~ smallMax：小规格，按 pEx
  t1Max: 750,      // smallMax+1 ~ t1Max：p1
  t2Max: 1000,     // t1Max+1 ~ t2Max：p2；再大是大规格，按 pEx
  p1: 37000,
  p2: 35000,
  pEx: 30000,
  loss: 0,         // 停料掉重 %，从各池重量里先扣掉
  orderTons: 60,
};

// 五个规格段：扣重、小、规格1、规格2、大
const SALE_BANDS = ["扣重", "小", "规格1", "规格2", "大"];

function saleBandLabels(r) {
  return [`≤${r.cutoff}g`, `${r.cutoff + 1}～${r.smallMax}g`, `${r.smallMax + 1}～${r.t1Max}g`, `${r.t1Max + 1}～${r.t2Max}g`, `≥${r.t2Max + 1}g`];
}

// 各段单价（元/吨）
function saleBandPrices(r) {
  return [0, r.pEx, r.p1, r.p2, r.pEx];
}

// P 数＝每公斤几条＝1000 ÷ 克/条（和经营页一样保留 1 位小数，截断）
function saleP(g) { return g > 0 ? Math.floor(10000 / g + 1e-9) / 10 : null; }
const saleFmtP = (p) => (p == null ? "—" : p.toFixed(p < 10 ? 2 : 1).replace(/0$/, ""));

// 五个规格段对应的 P 数范围（克数越小 P 越大）
function saleBandPLabels(r) {
  const p = (g) => saleFmtP(1000 / g);
  return [`P ${p(r.cutoff)} 以上`, `P ${p(r.smallMax)}～${p(r.cutoff + 1)}`, `P ${p(r.t1Max)}～${p(r.smallMax + 1)}`, `P ${p(r.t2Max)}～${p(r.t1Max + 1)}`, `P 不到 ${p(r.t2Max)}`];
}

// 只知道期报 P 数时：整池按平均克数归档
function salePriceForGrams(g, r) {
  if (!(g > 0)) return null;
  if (g <= r.cutoff) return 0;
  if (g <= r.smallMax) return r.pEx;
  if (g <= r.t1Max) return r.p1;
  if (g <= r.t2Max) return r.p2;
  return r.pEx;
}

// 分段吨数：各段有几吨 → 合计吨数、平均单价、扣重占比。没填返回 null
function saleFromSplit(split, r) {
  const t = SALE_BANDS.map((_, i) => Math.max(0, Number(split?.[i]) || 0));
  const tons = t.reduce((a, b) => a + b, 0);
  if (!tons) return null;
  const prices = saleBandPrices(r);
  const money = t.reduce((a, x, i) => a + x * prices[i], 0);
  return { tons, price: money / tons, deductPct: t[0] / tons * 100 };
}

// 单个池：pond = { name, weight(kg), count(尾) }；split = 五段吨数或空。
// 填了分段吨数就用填的吨数和各段价格；没填按期报重量和 P 数。
function saleEstimatePond(pond, r, split) {
  const weight = Number(pond.weight) || 0;
  const keep = 1 - (Number(r.loss) || 0) / 100;
  const avgG = weight > 0 && pond.count > 0 ? weight * 1000 / pond.count : null;
  const s = saleFromSplit(split, r);
  const tons = (s ? s.tons : weight / 1000) * keep;
  const price = s ? s.price : salePriceForGrams(avgG, r);
  return {
    name: pond.name, tons, reportTons: weight / 1000, avgG, price, p: saleP(avgG),
    amount: price == null ? null : tons * price,
    source: s ? "分段" : avgG ? "期报P数" : "缺数据",
    deductPct: s ? s.deductPct : avgG != null && avgG <= r.cutoff ? 100 : 0,
  };
}

// 合计：可供吨数、和订单比差多少、按订单量算的预估货款
function saleEstimate(ponds, r, splits = {}) {
  const rows = ponds.map((p) => saleEstimatePond(p, r, splits[p.name]));
  const priced = rows.filter((x) => x.price != null);
  const tons = rows.reduce((a, x) => a + x.tons, 0);
  const pricedTons = priced.reduce((a, x) => a + x.tons, 0);
  const amountAll = priced.reduce((a, x) => a + x.amount, 0);
  const avgPrice = pricedTons > 0 ? amountAll / pricedTons : null;
  // 选中池的整体 P 数＝总尾数 ÷ 总重量（期报，只算有尾数的池）
  const withCount = ponds.filter((p) => p.weight > 0 && p.count > 0);
  const wSum = withCount.reduce((a, p) => a + Number(p.weight), 0);
  const avgP = wSum > 0 ? saleP(wSum * 1000 / withCount.reduce((a, p) => a + Number(p.count), 0)) : null;
  const order = Number(r.orderTons) || 0;
  const sellTons = order > 0 ? Math.min(tons, order) : tons;
  return {
    rows, tons, order, gap: tons - order, avgPrice, amountAll, avgP,
    sellTons, amountOrder: avgPrice == null ? null : sellTons * avgPrice,
    missing: rows.filter((x) => x.price == null).map((x) => x.name),
  };
}

if (typeof module !== "undefined") {
  module.exports = { saleP, saleFmtP, saleBandPLabels, saleBandPrices, SALE_DEFAULT_RULES, SALE_BANDS, saleBandLabels, salePriceForGrams, saleFromSplit, saleEstimatePond, saleEstimate };
}
