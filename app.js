(() => {
  "use strict";

  const DATA_URL = "data/deals.json";
  const MIN_REVIEWS = 20;

  const $ = (id) => document.getElementById(id);
  const els = {
    fetched: $("fetched-at"),
    metrics: $("metrics"),
    bars: $("bars"),
    category: $("category"),
    sort: $("sort"),
    search: $("search"),
    hideFew: $("hide-few"),
    reset: $("reset"),
    status: $("status"),
    error: $("error"),
    deals: $("deals"),
    toTop: $("to-top"),
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
    );
  }

  function inr(n) {
    return Math.round(Number(n) || 0).toLocaleString("en-IN");
  }

  function formatCount(n) {
    return Number(n || 0).toLocaleString("en-IN");
  }

  function categoryOrder(counts) {
    const names = Object.keys(counts)
      .filter((name) => name !== "Other")
      .sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
    if (counts.Other) names.push("Other");
    return names;
  }

  function sortDeals(items, sort) {
    const copy = items.slice();
    copy.sort((a, b) => {
      if (sort === "priceLow") return a.price - b.price || b.pct - a.pct;
      if (sort === "priceHigh") return b.price - a.price || b.pct - a.pct;
      if (sort === "rating") return b.rating - a.rating || b.reviews - a.reviews;
      if (sort === "reviews") return b.reviews - a.reviews || b.rating - a.rating;
      return b.pct - a.pct || a.price - b.price;
    });
    return copy;
  }

  function normalizeItems(rawItems) {
    return (rawItems || []).map((item) => ({
      name: item.name || "",
      asin: item.asin || null,
      price: Number(item.current_price_inr),
      avg: Number(item.buyhatke_avg_inr),
      pct: Number(item.percent_below_avg),
      rating: Number(item.rating),
      reviews: Number(item.rating_count || 0),
      category: item.category || "Other",
      subcategory: item.subcategory || null,
      brand: item.brand || null,
      url: item.amazon_url || (item.asin ? "https://www.amazon.in/dp/" + item.asin : null),
    }));
  }

  function productCell(deal) {
    if (deal.url) {
      return `<a class="product-link" href="${escapeHtml(deal.url)}" rel="noopener noreferrer" target="_blank">${escapeHtml(deal.name)}</a>`;
    }
    return `<span class="no-asin">${escapeHtml(deal.name)} <em>(no ASIN)</em></span>`;
  }

  function reviewsCell(deal) {
    const count = formatCount(deal.reviews);
    if (deal.reviews < MIN_REVIEWS) {
      return `${count} <span class="few">few reviews</span>`;
    }
    return count;
  }

  function renderTable(rows) {
    const body = rows
      .map(
        (deal) => `<tr>
          <td class="product" data-label="Product">${productCell(deal)}</td>
          <td class="sub" data-label="Subcategory">${escapeHtml(deal.subcategory || "")}</td>
          <td class="num" data-label="Price">₹${inr(deal.price)}</td>
          <td class="num" data-label="Avg">₹${inr(deal.avg)}</td>
          <td class="num" data-label="% below"><span class="drop">${deal.pct.toFixed(1)}%</span></td>
          <td class="num rating" data-label="Rating">${deal.rating.toFixed(1)} <span class="star">★</span></td>
          <td class="num" data-label="Reviews">${reviewsCell(deal)}</td>
        </tr>`
      )
      .join("");
    return `<div class="table-wrap"><table class="deals">
      <thead>
        <tr>
          <th>Product</th>
          <th>Subcategory</th>
          <th class="num">Price</th>
          <th class="num">Avg</th>
          <th class="num">% below</th>
          <th class="num">Rating</th>
          <th class="num">Reviews</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table></div>`;
  }

  function renderMetrics(meta, items) {
    const few = meta.few_reviews_under_20 ?? items.filter((d) => d.reviews < MIN_REVIEWS).length;
    const other = (meta.category_counts && meta.category_counts.Other) || 0;
    const cats = Object.keys(meta.category_counts || {}).length;
    const rows = [
      [formatCount(items.length), "Qualifying deals"],
      [formatCount(cats), "Categories"],
      [formatCount(few), "Under 20 reviews"],
      [formatCount(other), "Grouped as Other"],
    ];
    els.metrics.innerHTML = rows
      .map(([num, lbl]) => `<li><span class="num">${escapeHtml(num)}</span><span class="lbl">${escapeHtml(lbl)}</span></li>`)
      .join("");
  }

  function renderBars(order, counts) {
    const max = Math.max(...order.map((name) => counts[name] || 0), 1);
    els.bars.innerHTML = order
      .map((name) => {
        const n = counts[name] || 0;
        const pct = (n / max) * 100;
        return `<button type="button" class="bar" data-cat="${escapeHtml(name)}" role="listitem">
          <span class="name">${escapeHtml(name)}</span>
          <span class="track"><span style="width:${pct}%"></span></span>
          <span class="count">${formatCount(n)}</span>
        </button>`;
      })
      .join("");
  }

  function fillCategorySelect(order, counts, total) {
    const opts = [`<option value="all">All categories (${formatCount(total)})</option>`]
      .concat(order.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)} (${formatCount(counts[name])})</option>`));
    els.category.innerHTML = opts.join("");
  }

  function apply(state) {
    const q = state.query.trim().toLowerCase();
    const rows = state.items.filter((deal) => {
      if (state.category !== "all" && deal.category !== state.category) return false;
      if (state.hideFew && deal.reviews < MIN_REVIEWS) return false;
      if (q) {
        const hay = `${deal.name} ${deal.subcategory || ""} ${deal.brand || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const sorted = sortDeals(rows, state.sort);
    const names = state.category === "all" ? state.order : [state.category];
    const groups = names
      .map((name) => ({ name, items: sorted.filter((d) => d.category === name) }))
      .filter((g) => g.items.length > 0);

    if (!sorted.length) {
      els.deals.innerHTML = `<p class="empty">No deals match these filters.</p>`;
    } else if (groups.length === 1) {
      els.deals.innerHTML = `<section class="group" aria-labelledby="all-heading">
        <h2 id="all-heading">${escapeHtml(groups[0].name)} (${formatCount(groups[0].items.length)})</h2>
        ${renderTable(groups[0].items)}
      </section>`;
    } else {
      els.deals.innerHTML = groups
        .map(
          (group, i) => `<section class="group" aria-labelledby="g-${i}">
            <h2 id="g-${i}">${escapeHtml(group.name)} (${formatCount(group.items.length)})</h2>
            ${renderTable(group.items)}
          </section>`
        )
        .join("");
    }

    const hiddenFew = state.hideFew
      ? state.items.filter((d) => d.reviews < MIN_REVIEWS && (state.category === "all" || d.category === state.category)).length
      : 0;
    els.status.textContent = `${formatCount(sorted.length)} shown${
      hiddenFew && state.hideFew ? ` · ${formatCount(hiddenFew)} hidden for few reviews` : ""
    }. Product names link to Amazon.in.`;
  }

  async function init() {
    let payload;
    try {
      const res = await fetch(DATA_URL, { cache: "no-cache" });
      if (!res.ok) throw new Error(`Could not load ${DATA_URL} (${res.status})`);
      payload = await res.json();
    } catch (err) {
      els.fetched.textContent = "Could not load deals";
      els.error.hidden = false;
      els.error.textContent =
        "The deals list could not be loaded. If you opened this file directly, serve the folder with a local web server (for example: python3 -m http.server) so data/deals.json can be fetched.";
      console.error(err);
      return;
    }

    const meta = payload.meta || {};
    const items = normalizeItems(payload.items);
    const counts = meta.category_counts || items.reduce((acc, d) => {
      acc[d.category] = (acc[d.category] || 0) + 1;
      return acc;
    }, {});
    const order = categoryOrder(counts);

    const when = meta.fetched_at || "";
    const zone = meta.timezone || "Asia/Calcutta";
    els.fetched.textContent = when
      ? `Updated ${when}${/IST|Asia\/Calcutta/.test(when) ? "" : ` (${zone})`}`
      : `Updated time unavailable (${zone})`;

    renderMetrics(meta, items);
    renderBars(order, counts);
    fillCategorySelect(order, counts, items.length);

    const state = {
      items,
      order,
      category: "all",
      sort: "drop",
      query: "",
      hideFew: false,
    };

    const redraw = () => apply(state);
    els.category.addEventListener("change", () => {
      state.category = els.category.value;
      redraw();
    });
    els.sort.addEventListener("change", () => {
      state.sort = els.sort.value;
      redraw();
    });
    let searchTimer = null;
    els.search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.query = els.search.value;
        redraw();
      }, 120);
    });
    els.hideFew.addEventListener("change", () => {
      state.hideFew = els.hideFew.checked;
      redraw();
    });
    els.reset.addEventListener("click", () => {
      state.category = "all";
      state.sort = "drop";
      state.query = "";
      state.hideFew = false;
      els.category.value = "all";
      els.sort.value = "drop";
      els.search.value = "";
      els.hideFew.checked = false;
      redraw();
    });
    els.bars.addEventListener("click", (event) => {
      const btn = event.target.closest(".bar");
      if (!btn) return;
      const name = btn.getAttribute("data-cat");
      els.category.value = name;
      state.category = name;
      $("filters").scrollIntoView({ block: "start" });
      redraw();
    });

    redraw();
  }

  const onScroll = () => {
    els.toTop.hidden = window.scrollY < 600;
  };
  els.toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  window.addEventListener("scroll", onScroll, { passive: true });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
