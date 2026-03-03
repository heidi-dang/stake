// Lightweight Charts setup and management
const chartOptions = {
    layout: { textColor: '#c5c6c7', background: { type: 'solid', color: 'transparent' } },
    grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
    timeScale: { borderColor: 'rgba(255,255,255,0.1)' }
};

let mainChart;
let profitSeries, ema5Series, ema20Series, rollSeries;

function initMainChart() {
    const container = document.getElementById('tvchart');
    if (!container) return;

    mainChart = LightweightCharts.createChart(container, chartOptions);

    profitSeries = mainChart.addAreaSeries({
        lineColor: '#66fcf1',
        topColor: 'rgba(102, 252, 241, 0.4)',
        bottomColor: 'rgba(102, 252, 241, 0.0)',
        lineWidth: 2,
        title: 'PnL'
    });

    ema5Series = mainChart.addLineSeries({ color: '#f39c12', lineWidth: 1, title: 'EMA 5' });
    ema20Series = mainChart.addLineSeries({ color: '#8e44ad', lineWidth: 1, title: 'EMA 20' });

    rollSeries = mainChart.addHistogramSeries({
        color: '#2ed573',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        title: 'Rolls'
    });

    rollSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
}

function updateChart() {
    fetch('/chart_data').then(r => r.json()).then(data => {
        if (!data || data.length === 0) return;
        if (!profitSeries) return;

        const mappedData = data.map((d, i) => { return { ...d, time: d.bets || i }; });
        const dedup = Array.from(new Map(mappedData.map(item => [item.time, item])).values());
        dedup.sort((a, b) => a.time - b.time);

        try {
            profitSeries.setData(dedup.map(d => ({ time: d.time, value: d.profit || 0 })));
            if (dedup.length > 0 && dedup[0].ema5 !== undefined && dedup[0].ema5 !== null) {
                ema5Series.setData(dedup.filter(d => d.ema5 !== null).map(d => ({ time: d.time, value: d.ema5 || 0 })));
                ema20Series.setData(dedup.filter(d => d.ema20 !== null).map(d => ({ time: d.time, value: d.ema20 || 0 })));
                rollSeries.setData(dedup.map(d => ({
                    time: d.time,
                    value: d.roll_result || 0,
                    color: d.roll_result > 50 ? '#2ed573' : '#ff4757'
                })));
            }
        } catch (e) {
            console.warn("Chart data update error:", e);
        }
    });
}

// Global simulator chart
window.gork_sim_chart = null;

function renderSimChart(res, pnl) {
    const chartWrap = document.getElementById('sim-chart-wrap');
    if (!chartWrap) return;

    chartWrap.style.display = 'block';
    const chartEl = document.getElementById('sim-chart');
    if (!chartEl) return;

    if (window.gork_sim_chart) {
        try { window.gork_sim_chart.remove(); } catch (e) { }
        window.gork_sim_chart = null;
    }

    setTimeout(() => {
        try {
            const profitColor = pnl >= 0 ? '#2ed573' : '#ff4757';
            const lw = window.LightweightCharts || (typeof LightweightCharts !== 'undefined' ? LightweightCharts : null);
            if (!lw || typeof lw.createChart !== 'function') {
                console.error("LightweightCharts library missing or invalid.");
                return;
            }

            window.gork_sim_chart = lw.createChart(chartEl, {
                width: chartEl.clientWidth || 600,
                height: 298,
                layout: { background: { type: 'solid', color: '#0d1117' }, textColor: '#a0aec0' }
            });

            const chartObj = window.gork_sim_chart;
            const areaSeries = chartObj.addAreaSeries({
                lineColor: profitColor,
                topColor: profitColor + '44',
                bottomColor: profitColor + '08',
                lineWidth: 2
            });

            const chartData = res.equity_curve.map((pt, i) => ({
                time: i + 1,
                value: parseFloat(pt.value)
            }));
            areaSeries.setData(chartData);

            areaSeries.createPriceLine({
                price: res.starting_balance,
                color: 'rgba(255,255,255,0.3)',
                lineWidth: 1,
                lineStyle: 2,
                title: 'Start'
            });

            chartObj.timeScale().fitContent();
        } catch (err) {
            console.error("Simulator Chart Error:", err);
        }
    }, 200);
}
