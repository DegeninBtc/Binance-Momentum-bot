import type { Primitive, TradeItem } from "./types";

export function asNumber(value: Primitive): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function trimNumber(value: Primitive, maxDigits: number, minDigits = 0): string {
  const parsed = asNumber(value);
  if (parsed === null) {
    return value === null || value === undefined || value === "" ? "--" : String(value);
  }
  return parsed.toLocaleString("en-US", {
    minimumFractionDigits: minDigits,
    maximumFractionDigits: maxDigits,
  });
}

export function formatScore(value: Primitive): string {
  return trimNumber(value, 1, 1);
}

export function formatPercent(value: Primitive): string {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  return `${trimNumber(value, 2, 2)}%`;
}

export function signedPercent(value: Primitive): string {
  const parsed = asNumber(value);
  if (parsed === null) {
    return "--";
  }
  const sign = parsed > 0 ? "+" : "";
  return `${sign}${trimNumber(parsed, 2, 2)}%`;
}

export function formatQty(value: Primitive): string {
  return trimNumber(value, 6);
}

export function formatPrice(value: Primitive): string {
  return trimNumber(value, 8);
}

export function formatMoney(value: Primitive, quoteAsset?: string): string {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  const text = trimNumber(value, 2, 2);
  return quoteAsset ? `${text} ${quoteAsset}` : text;
}

export function signedMoney(value: Primitive, quoteAsset?: string): string {
  const parsed = asNumber(value);
  if (parsed === null) {
    return "--";
  }
  const sign = parsed > 0 ? "+" : parsed < 0 ? "-" : "";
  return `${sign}${formatMoney(Math.abs(parsed), quoteAsset)}`;
}

export function tradeAmount(item: TradeItem): number | null {
  const quoteAmount = asNumber(item.quote_amount);
  if (quoteAmount !== null) {
    return quoteAmount;
  }
  const quantity = asNumber(item.quantity);
  const price = asNumber(item.price);
  return quantity !== null && price !== null ? quantity * price : null;
}

export function actionLabel(action = ""): string {
  if (action.includes("BUY")) {
    return "买入";
  }
  if (action.includes("MANUAL")) {
    return "手动平仓";
  }
  if (action.includes("LIQUIDATION")) {
    return "模拟爆仓";
  }
  if (action.includes("SELL")) {
    if (action.includes("TAKE_PROFIT")) {
      return "止盈卖出";
    }
    if (action.includes("STOP")) {
      return "止损卖出";
    }
    return "卖出";
  }
  return action || "--";
}

export function formatTime(value?: string): string {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function stopModeLabel(mode?: string): string {
  const text = String(mode || "percent");
  const parts = [];
  if (text.includes("fixed-usdt")) {
    parts.push("固定金额");
  }
  if (text.includes("trailing")) {
    parts.push("移动止盈");
  } else if (text.includes("breakeven")) {
    parts.push("保本止损");
  } else {
    parts.push("百分比止损");
  }
  return parts.join("+");
}

export function textValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join(",");
  }
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}
