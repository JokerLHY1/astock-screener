let allData = [];
let sortKey = 'score';
let sortAsc = false;

async function loadData() {
  try {
    const res = await fetch('data/snapshot.json');
    allData = await res.json();
    initIndustry();
    renderTable();
    document.getElementById('result-count').textContent = `共 ${allData.length} 只股票`;
  } catch (e) {
    document.getElementById('result-count').textContent = '数据加载失败';
    console.error(e);
  }
}

function initIndustry() {
  const sel = document.getElementById('industry');
  const industries = [...new Set(allData.map(s => s.industry).filter(Boolean))].sort();
  industries.forEach(i => sel.add(new Option(i, i)));
}

function calcScore(s) {
  const wTech = parseFloat(document.getElementById('w-tech').value) || 70;
  const wFund = parseFloat(document.getElementById('w-fund').value) || 30;
  const techRaw = s.tech_total || 0;
  const fundRaw = s.fund_total || 0;
  // 归一化到百分制（tech_total 满分4，fund_total 满分约30）
  const techScore = (techRaw / 4) * 100 * (wTech / 100);
  const fundScore = (fundRaw / 30) * 100 * (wFund / 100);
  return Math.round((techScore + fundScore) * 10) / 10;
}

function renderTable() {
  const tbody = document.querySelector('#stock-table tbody');
  const filtered = filterData();
  filtered.forEach(s => { s._score = calcScore(s); });
  filtered.sort((a, b) => {
    let va = sortKey === 'score' ? a._score : (a[sortKey] || 0);
    let vb = sortKey === 'score' ? b._score : (b[sortKey] || 0);
    return sortAsc ? va - vb : vb - va;
  });

  tbody.innerHTML = filtered.map(s => `
    <tr>
      <td>${s.code}</td>
      <td>${s.name}</td>
      <td>${s.industry || '-'}</td>
      <td>${s.price != null ? s.price.toFixed(2) : '-'}</td>
      <td><strong>${s._score}</strong></td>
      <td>${s.tech_total || 0}/4</td>
      <td>${s.fund_total || 0}</td>
      <td><button class="detail-btn" onclick="showDetail('${s.code}')">详情</button></td>
    </tr>
  `).join('');
  document.getElementById('result-count').textContent = `筛选结果 ${filtered.length} / ${allData.length} 只`;
}

function filterData() {
  const indSel = document.getElementById('industry');
  const selIndustries = Array.from(indSel.selectedOptions).map(o => o.value);
  const pMin = parseFloat(document.getElementById('price-min').value);
  const pMax = parseFloat(document.getElementById('price-max').value);
  const fMa = document.getElementById('f-ma').checked;
  const fMacd = document.getElementById('f-macd').checked;
  const fKdj = document.getElementById('f-kdj').checked;
  const fVol = document.getElementById('f-vol').checked;

  return allData.filter(s => {
    if (selIndustries.length && !selIndustries.includes(s.industry)) return false;
    if (pMin != null && !isNaN(pMin) && s.price < pMin) return false;
    if (pMax != null && !isNaN(pMax) && s.price > pMax) return false;
    if (fMa && !s.ma_bull) return false;
    if (fMacd && !s.macd_gold) return false;
    if (fKdj && !s.kdj_gold) return false;
    if (fVol && !s.vol_breakout) return false;
    return true;
  });
}

// 事件绑定
document.getElementById('btn-filter').onclick = renderTable;
document.getElementById('btn-reset').onclick = () => {
  document.getElementById('industry').selectedIndex = -1;
  document.getElementById('price-min').value = '';
  document.getElementById('price-max').value = '';
  document.getElementById('f-ma').checked = false;
  document.getElementById('f-macd').checked = false;
  document.getElementById('f-kdj').checked = false;
  document.getElementById('f-vol').checked = false;
  document.getElementById('w-tech').value = 70;
  document.getElementById('w-fund').value = 30;
  renderTable();
};
document.querySelectorAll('th').forEach(th => {
  th.onclick = () => {
    const key = th.dataset.key;
    if (!key) return;
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = false; }
    renderTable();
  };
});
document.getElementById('w-tech').onchange = document.getElementById('w-fund').onchange = renderTable;

// 详情弹窗
function showDetail(code) {
  const s = allData.find(x => x.code === code);
  if (!s) return;
  const body = document.getElementById('detail-body');
  body.innerHTML = `
    <h2 style="margin-bottom:12px;">${s.name} <span style="color:#64748b;font-size:0.6em">${s.code}</span></h2>
    <div class="detail-section">
      <h3>📈 技术面</h3>
      <div class="detail-item"><span>5/20均线多头</span><span class="${s.ma_bull ? 'bool-yes' : 'bool-no'}">${s.ma_bull ? '✅ 是' : '❌ 否'}</span></div>
      <div class="detail-item"><span>MACD金叉</span><span class="${s.macd_gold ? 'bool-yes' : 'bool-no'}">${s.macd_gold ? '✅ 是' : '❌ 否'}</span></div>
      <div class="detail-item"><span>KDJ金叉</span><span class="${s.kdj_gold ? 'bool-yes' : 'bool-no'}">${s.kdj_gold ? '✅ 是' : '❌ 否'}</span></div>
      <div class="detail-item"><span>放量突破</span><span class="${s.vol_breakout ? 'bool-yes' : 'bool-no'}">${s.vol_breakout ? '✅ 是' : '❌ 否'}</span></div>
      <div class="detail-item"><span>技术面总分</span><span>${s.tech_total || 0} / 4</span></div>
    </div>
    <div class="detail-section">
      <h3>📊 基本面</h3>
      <div class="detail-item"><span>ROE</span><span>${s.roe != null ? s.roe.toFixed(2) + '%' : '-'}</span></div>
      <div class="detail-item"><span>PE</span><span>${s.pe != null ? s.pe.toFixed(2) : '-'}</span></div>
      <div class="detail-item"><span>PB</span><span>${s.pb != null ? s.pb.toFixed(2) : '-'}</span></div>
      <div class="detail-item"><span>ROE评分</span><span>${s.roe_score || 0}</span></div>
      <div class="detail-item"><span>基本面总分</span><span>${s.fund_total || 0}</span></div>
    </div>
    <div class="detail-section">
      <h3>🎯 综合评分</h3>
      <div class="detail-item"><span>综合分</span><span><strong>${calcScore(s)}</strong></span></div>
      <div class="detail-item"><span>行业</span><span>${s.industry || '-'}</span></div>
      <div class="detail-item"><span>当前股价</span><span>${s.price != null ? s.price.toFixed(2) : '-'}</span></div>
    </div>
    <p style="color:#64748b;font-size:0.75em;margin-top:12px;">⚠️ 数据由引擎预计算，非实时，不构成投资建议</p >
  `;
  document.getElementById('detail-modal').classList.remove('hidden');
}
document.querySelector('.close').onclick = () => document.getElementById('detail-modal').classList.add('hidden');
document.getElementById('detail-modal').onclick = e => { if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden'); };

loadData();
