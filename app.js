// ============ 配置 ============

const REPO_OWNER = 'JokerLHY1';

const REPO_NAME = 'astock-screener';

const WORKFLOW_ID = 'update-data.yml';

const GH_TOKEN = '';



// ============ 全局状态 ============

let allData = [];

let sortKey = 'score';

let sortAsc = false;

let selectedIndustries = [];

let selectedCodes = [];

let allIndustries = [];



// ============ 数据加载 ============

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



// ============ 行业下拉 ============

function initIndustry() {

  allIndustries = [...new Set(allData.map(s => s.industry).filter(Boolean))].sort();

  renderIndustryOptions('');

  setupDropdownEvents();

}



function renderIndustryOptions(keyword) {

  const opts = document.getElementById('industryOptions');

  const kw = keyword.trim().toLowerCase();

  const list = allIndustries.filter(i => !kw || i.toLowerCase().includes(kw));



  opts.innerHTML = list.map(i => {

    const checked = selectedIndustries.includes(i) ? 'checked' : '';

    return `<label class="ind-option"><input type="checkbox" value="${i}" ${checked}> ${i}</label>`;

  }).join('');



  opts.querySelectorAll('input[type=checkbox]').forEach(cb => {

    cb.onchange = () => {

      if (cb.checked) {

        if (!selectedIndustries.includes(cb.value)) selectedIndustries.push(cb.value);

      } else {

        selectedIndustries = selectedIndustries.filter(v => v !== cb.value);

      }

      updateIndustryTags();

    };

  });



  updateIndustryTags();

}



function updateIndustryTags() {

  const tagsBox = document.getElementById('industryTags');

  tagsBox.innerHTML = selectedIndustries.map(i =>

    `<span class="tag">${i}<span class="tag-x" data-v="${i}">×</span></span>`

  ).join('');



  tagsBox.querySelectorAll('.tag-x').forEach(x => {

    x.onclick = (e) => {

      e.stopPropagation();

      selectedIndustries = selectedIndustries.filter(v => v !== x.dataset.v);

      renderIndustryOptions(document.getElementById('industryFilter').value);

    };

  });



  document.getElementById('industryCount').textContent = `已选 ${selectedIndustries.length}`;

  const search = document.getElementById('industrySearch');

  search.placeholder = selectedIndustries.length ? `${selectedIndustries.length} 个行业已选` : '搜索行业...';

}



function setupDropdownEvents() {

  const dropdown = document.getElementById('industryDropdown');

  const panel = document.getElementById('industryPanel');

  const filterInput = document.getElementById('industryFilter');



  dropdown.querySelector('.dropdown-header').onclick = (e) => {

    if (e.target.tagName === 'INPUT') return;

    panel.classList.toggle('open');

    if (panel.classList.contains('open')) filterInput.focus();

  };



  document.addEventListener('click', (e) => {

    if (!dropdown.contains(e.target)) panel.classList.remove('open');

  });



  filterInput.oninput = (e) => renderIndustryOptions(e.target.value);



  document.getElementById('industrySelectAll').onclick = () => {

    const visible = [...document.querySelectorAll('#industryOptions .ind-option input')];

    visible.forEach(cb => {

      cb.checked = true;

      if (!selectedIndustries.includes(cb.value)) selectedIndustries.push(cb.value);

    });

    updateIndustryTags();

  };



  document.getElementById('industryClear').onclick = () => {

    selectedIndustries = [];

    renderIndustryOptions(filterInput.value);

  };



  document.getElementById('industryConfirm').onclick = () => {

    panel.classList.remove('open');

    renderTable();

  };

}



// ============ 个股搜索+勾选 ============

function initStockSearch() {

  const searchInput = document.getElementById('stockSearchInput');

  const resultsBox = document.getElementById('stockSearchResults');

  if (!searchInput) return;



  let debounceTimer;

  searchInput.oninput = (e) => {

    clearTimeout(debounceTimer);

    const kw = e.target.value.trim().toLowerCase();

    if (kw.length < 1) {

      resultsBox.innerHTML = '';

      resultsBox.classList.remove('open');

      return;

    }

    debounceTimer = setTimeout(() => {

      const matched = allData.filter(s => {

        return s.code.toLowerCase().includes(kw) || (s.name && s.name.toLowerCase().includes(kw));

      }).slice(0, 10);



      if (matched.length === 0) {

        resultsBox.innerHTML = '<div class="search-empty">无匹配结果</div>';

      } else {

        resultsBox.innerHTML = matched.map(s => {

          const checked = selectedCodes.includes(s.code) ? 'checked' : '';

          return `<label class="stock-search-item">

            <input type="checkbox" value="${s.code}" ${checked}>

            <span class="stock-code">${s.code}</span>

            <span class="stock-name">${s.name}</span>

            <span class="stock-industry">${s.industry || ''}</span>

          </label>`;

        }).join('');

      }

      resultsBox.classList.add('open');



      resultsBox.querySelectorAll('input[type=checkbox]').forEach(cb => {

        cb.onchange = () => {

          if (cb.checked) {

            if (!selectedCodes.includes(cb.value)) selectedCodes.push(cb.value);

          } else {

            selectedCodes = selectedCodes.filter(c => c !== cb.value);

          }

          updateSelectedStockTags();

        };

      });

    }, 300);

  };



  document.addEventListener('click', (e) => {

    if (!searchInput.contains(e.target) && !resultsBox.contains(e.target)) {

      resultsBox.classList.remove('open');

    }

  });

}



function updateSelectedStockTags() {

  const tagsBox = document.getElementById('selectedStockTags');

  if (!tagsBox) return;



  const selectedStocks = allData.filter(s => selectedCodes.includes(s.code));

  tagsBox.innerHTML = selectedStocks.map(s =>

    `<span class="tag">${s.name || s.code}<span class="tag-x" data-code="${s.code}">×</span></span>`

  ).join('');



  tagsBox.querySelectorAll('.tag-x').forEach(x => {

    x.onclick = (e) => {

      e.stopPropagation();

      selectedCodes = selectedCodes.filter(c => c !== x.dataset.code);

      updateSelectedStockTags();

      const cb = document.querySelector(`#stockSearchResults input[value="${x.dataset.code}"]`);

      if (cb) cb.checked = false;

    };

  });



  const countEl = document.getElementById('selectedStockCount');

  if (countEl) countEl.textContent = `已选 ${selectedCodes.length} 只`;

}



// ============ 评分 ============

function calcScore(s) {

  const wTech = parseFloat(document.getElementById('w-tech').value) || 70;

  const wFund = parseFloat(document.getElementById('w-fund').value) || 30;

  const techRaw = s.tech_total || 0;

  const fundRaw = s.fund_total || 0;

  const techScore = (techRaw / 4) * 100 * (wTech / 100);

  const fundScore = (fundRaw / 30) * 100 * (wFund / 100);

  return Math.round((techScore + fundScore) * 10) / 10;

}



// ============ 表格渲染 ============

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

  const pMin = parseFloat(document.getElementById('price-min').value);

  const pMax = parseFloat(document.getElementById('price-max').value);

  const fMa = document.getElementById('f-ma').checked;

  const fMacd = document.getElementById('f-macd').checked;

  const fKdj = document.getElementById('f-kdj').checked;

  const fVol = document.getElementById('f-vol').checked;



  return allData.filter(s => {

    if (selectedIndustries.length && !selectedIndustries.includes(s.industry)) return false;

    if (selectedCodes.length > 0 && !selectedCodes.includes(s.code)) return false;

    if (pMin != null && !isNaN(pMin) && s.price < pMin) return false;

    if (pMax != null && !isNaN(pMax) && s.price > pMax) return false;

    if (fMa && !s.ma_bull) return false;

    if (fMacd && !s.macd_gold) return false;

    if (fKdj && !s.kdj_gold) return false;

    if (fVol && !s.vol_breakout) return false;

    return true;

  });

}



// ============ 事件绑定 ============

document.getElementById('btn-filter').onclick = renderTable;

document.getElementById('btn-reset').onclick = () => {

  selectedIndustries = [];

  selectedCodes = [];

  document.getElementById('industryFilter').value = '';

  renderIndustryOptions('');

  updateSelectedStockTags();

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



// ============ 更新数据按钮 ============

document.getElementById('btn-update').onclick = async () => {

  const token = GH_TOKEN || localStorage.getItem('gh_token');

  if (!token) {

    const input = prompt('首次使用需填写 GitHub Personal Access Token（仅本地保存）：');

    if (!input) return;

    localStorage.setItem('gh_token', input.trim());

    triggerUpdate(input.trim());

    return;

  }

  triggerUpdate(token);

};



async function triggerUpdate(token) {

  const btn = document.getElementById('btn-update');

  const btnText = btn.querySelector('.btn-text');

  const spinner = btn.querySelector('.btn-spinner');



  // 获取要更新的代码

  let codes = [];

  if (selectedCodes.length > 0) {

    codes = [...selectedCodes];

  } else {

    // 检查是否真的有筛选条件

    const hasFilter = selectedIndustries.length > 0

      || document.getElementById('price-min').value

      || document.getElementById('price-max').value

      || document.getElementById('f-ma').checked

      || document.getElementById('f-macd').checked

      || document.getElementById('f-kdj').checked

      || document.getElementById('f-vol').checked;



    if (hasFilter) {

      codes = filterData().map(s => s.code);

      if (codes.length > 100) {

        const ok = confirm(`筛选结果共 ${codes.length} 只，全部更新可能较慢。继续？`);

        if (!ok) return;

      }

    }

    // 无勾选 + 无筛选 → codes 保持空数组，走 daily

  }



  // 长度保护：GitHub Actions input 有长度限制

  if (codes.join(',').length > 400) {

    alert(`⚠️ 股票数量过多（${codes.length}只），参数可能超出限制。\n已截取前40只进行更新。`);

    codes = codes.slice(0, 40);

  }



  if (codes.length === 0) {

    // 无 codes → 走每日增量

    btn.disabled = true;

    btnText.textContent = '更新中(每日增量)...';

    spinner.classList.remove('hidden');



    try {

      const res = await fetch(

        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_ID}/dispatches`,

        {

          method: 'POST',

          headers: {

            'Accept': 'application/vnd.github+json',

            'Authorization': `Bearer ${token}`,

            'Content-Type': 'application/json',

          },

          body: JSON.stringify({

            ref: 'main',

            inputs: {

              mode: 'daily',

              sectors: ''

            }

          }),

        }

      );



      if (res.status === 204) {

        alert(`✅ 已触发每日增量更新！\n工作流跑完后刷新页面即可看到最新数据。\n\n点击"确定"开始轮询状态...`);

        pollWorkflowStatus(token);

      } else {

        const err = await res.json().catch(() => ({}));

        throw new Error(err.message || `HTTP ${res.status}`);

      }

    } catch (e) {

      alert('❌ 触发失败：' + e.message);

      btn.disabled = false;

      btnText.textContent = '🔄 更新数据';

      spinner.classList.add('hidden');

    }

    return;

  }



  // 有 codes → 走指定代码更新

  btn.disabled = true;

  btnText.textContent = `更新中(${codes.length}只)...`;

  spinner.classList.remove('hidden');



  try {

    const res = await fetch(

      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_ID}/dispatches`,

      {

        method: 'POST',

        headers: {

          'Accept': 'application/vnd.github+json',

          'Authorization': `Bearer ${token}`,

          'Content-Type': 'application/json',

        },

        body: JSON.stringify({

          ref: 'main',

          inputs: {

            codes: codes.join(','),

            sectors: ''

          }

        }),

      }

    );



    if (res.status === 204) {

      alert(`✅ 已触发更新！共 ${codes.length} 只股票。\n工作流跑完后刷新页面即可看到最新数据。\n\n点击"确定"开始轮询状态...`);

      pollWorkflowStatus(token);

    } else {

      const err = await res.json().catch(() => ({}));

      throw new Error(err.message || `HTTP ${res.status}`);

    }

  } catch (e) {

    alert('❌ 触发失败：' + e.message);

    btn.disabled = false;

    btnText.textContent = '🔄 更新数据';

    spinner.classList.add('hidden');

  }

}



async function pollWorkflowStatus(token) {

  const btn = document.getElementById('btn-update');

  const btnText = btn.querySelector('.btn-text');

  const spinner = btn.querySelector('.btn-spinner');



  try {

    const res = await fetch(

      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/runs?per_page=5`,

      { headers: { 'Authorization': `Bearer ${token}` } }

    );

    const data = await res.json();

    const latest = data.workflow_runs && data.workflow_runs.find(r =>

      r.path && r.path.includes('update-data')

    ) || (data.workflow_runs && data.workflow_runs[0]);



    if (!latest) throw new Error('未找到 workflow run');



    if (latest.status === 'completed') {

      btn.disabled = false;

      spinner.classList.add('hidden');

      if (latest.conclusion === 'success') {

        btnText.textContent = '✅ 更新完成';

        const refresh = confirm('数据已更新完成！是否刷新页面查看最新数据？');

        if (refresh) location.reload();

        setTimeout(() => { btnText.textContent = '🔄 更新数据'; }, 5000);

      } else {

        btnText.textContent = '❌ 更新失败';

        alert(`工作流执行失败（${latest.conclusion}），请到 GitHub Actions 页面查看日志。`);

        setTimeout(() => { btnText.textContent = '🔄 更新数据'; }, 5000);

      }

    } else {

      btnText.textContent = `更新中... ${latest.status}`;

      setTimeout(() => pollWorkflowStatus(token), 15000);

    }

  } catch (e) {

    btn.disabled = false;

    spinner.classList.add('hidden');

    btnText.textContent = '🔄 更新数据';

    alert('状态查询失败：' + e.message + '\n请稍后手动刷新页面。');

  }

}



// ============ 详情弹窗 ============

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

    <p style="color:#64748b;font-size:0.75em;margin-top:12px;">⚠️ 数据由引擎预计算，非实时，不构成投资建议</p>

  `;

  document.getElementById('detail-modal').classList.remove('hidden');

}

document.querySelector('.close').onclick = () => document.getElementById('detail-modal').classList.add('hidden');

document.getElementById('detail-modal').onclick = e => { if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden'); };



// ============ 初始化 ============

loadData();

initStockSearch();
