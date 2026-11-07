// 小当家大采购. Mode "cart": put q.order on the shelf into the cart and type the total. The running total is hidden on
// purpose, the sum is the exercise. Figures: "shop-shelf" (q.items, optional q.wallet) and "shop-deal" (q.stores).
(() => {
  'use strict';
  const { el, button } = PK;

  function goods(item) {
    const card = el('div', 'goods');
    card.append(el('span', 'goods-emoji', item.emoji), el('span', 'goods-name', item.name), el('span', 'goods-price', `${item.price}元/${item.unit}`));
    return card;
  }
  const orderText = q => q.order.map(o => `${o.count}${q.items[o.item].unit}${q.items[o.item].name}`).join('、');
  const cartText = (q, cart) => cart.map((n, i) => (n ? `${n}${q.items[i].unit}${q.items[i].name}` : '')).filter(Boolean).join('、') || '什么也没拿';
  const wanted = q => q.items.map((_, i) => q.order.find(o => o.item === i)?.count ?? 0);
  const CART_MAX = 9;

  PK.modes.cart = {
    init: q => ({ cart: q.items.map(() => 0), parts: [''], active: 0 }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-cart');
      wrap.append(el('p', 'prompt', q.prompt));
      const shelf = el('div', 'shelf');
      q.items.forEach((item, i) => {
        const card = goods(item);
        const row = el('div', 'goods-keys');
        const minus = button('－', () => ctx.change(s => (s.cart[i] = Math.max(0, s.cart[i] - 1))), 'goods-key');
        const plus = button('＋', () => ctx.change(s => (s.cart[i] = Math.min(CART_MAX, s.cart[i] + 1))), 'goods-key');
        minus.disabled = ctx.locked || !a.cart[i];
        plus.disabled = ctx.locked;
        minus.setAttribute('aria-label', '少拿一' + item.unit + item.name);
        plus.setAttribute('aria-label', '多拿一' + item.unit + item.name);
        row.append(minus, el('b', 'goods-count', String(a.cart[i])), plus);
        card.append(row);
        shelf.append(card);
      });
      wrap.append(shelf);
      const basket = el('p', 'basket');
      basket.textContent = '🛒 ' + a.cart.map((n, i) => q.items[i].emoji.repeat(n)).join(' ').trim();
      if (!a.cart.some(Boolean)) basket.textContent = '🛒 购物车还是空的';
      wrap.append(basket);
      wrap.append(PK.numberFields([{ before: '一共', answer: q.total, unit: '元' }], a, ctx));
      return wrap;
    },
    value: (q, a) => {
      if (!a.cart.some(Boolean)) return { incomplete: '先按单子把东西放进购物车。' };
      if (!a.parts[0]) return { incomplete: '再算算一共多少元，填在下面。' };
      return { value: { cart: [...a.cart], total: a.parts[0] } };
    },
    check(q, value) {
      const want = wanted(q);
      return want.every((n, i) => n === value.cart[i]) && PK.sameNumber(value.total, q.total);
    },
    solution: q => `${orderText(q)}，${q.calcText}`,
    describe: q => q.prompt,
    diagnose(q, value) {
      if (wanted(q).some((n, i) => n !== value.cart[i])) return `购物车里是：${cartText(q, value.cart)}，和单子不一样。`;
      return `东西拿对了，总价算错了（你填的是 ${value.total} 元）。`;
    },
  };

  PK.figures['shop-shelf'] = q => {
    const box = el('div', 'shop-figure');
    if (q.items) {
      const shelf = el('div', 'shelf');
      q.items.forEach(i => shelf.append(goods(i)));
      box.append(shelf);
    }
    if (q.wallet !== undefined) box.append(el('p', 'wallet', `👛 钱包里有 ${q.wallet} 元`));
    return box;
  };
  PK.figures['shop-deal'] = q => {
    const row = el('div', 'deal-row');
    for (const store of q.stores) {
      const card = el('div', 'deal-card');
      card.append(el('b', '', '🏪 ' + store.name), el('span', 'goods-emoji', q.emoji),
        el('span', 'deal-tag', `${store.count}${q.unit} ${store.total}元`));
      row.append(card);
    }
    return row;
  };
})();
