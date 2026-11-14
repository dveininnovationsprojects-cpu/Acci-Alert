/**
 * AcciAlert - Real-Time Speed & Alcohol Monitoring Charts
 * Rolling 60-second window using Chart.js.
 * Author: Muniyappan
 */

class LiveChartsManager {
  constructor() {
    this.speedChart = null;
    this.alcoholChart = null;
    this.maxDataPoints = 60; // 60 seconds of history

    this.speedHistory = [];
    this.alcoholHistory = [];
    this.timeLabels = [];

    this.isPaused = false;
    this.initCharts();
  }

  initCharts() {
    const now = new Date();
    // Initialize 60 empty seconds
    for (let i = this.maxDataPoints - 1; i >= 0; i--) {
      const past = new Date(now.getTime() - i * 1000);
      this.timeLabels.push(past.toTimeString().split(" ")[0]);
      this.speedHistory.push(0);
      this.alcoholHistory.push(180);
    }

    this.initSpeedChart();
    this.initAlcoholChart();
  }

  initSpeedChart() {
    const canvas = document.getElementById("speedChartCanvas");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, 160);
    gradient.addColorStop(0, "rgba(202, 138, 4, 0.35)");
    gradient.addColorStop(1, "rgba(202, 138, 4, 0.0)");

    this.speedChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [...this.timeLabels],
        datasets: [{
          label: "Vehicle Speed (km/h)",
          data: [...this.speedHistory],
          borderColor: "#CA8A04",
          borderWidth: 2.2,
          backgroundColor: gradient,
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#CA8A04"
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: "index",
            intersect: false,
            backgroundColor: "#111827",
            titleColor: "#CA8A04",
            bodyColor: "#FFFFFF",
            borderColor: "rgba(202, 138, 4, 0.4)",
            borderWidth: 1,
            callbacks: {
              label: (ctx) => `Speed: ${ctx.parsed.y.toFixed(1)} km/h`
            }
          }
        },
        scales: {
          x: {
            display: true,
            grid: { color: "rgba(17, 24, 39, 0.08)" },
            ticks: {
              color: "#6B7280",
              font: { size: 10 },
              maxTicksLimit: 6
            }
          },
          y: {
            display: true,
            min: 0,
            max: 100,
            grid: { color: "rgba(17, 24, 39, 0.08)" },
            ticks: {
              color: "#6B7280",
              font: { size: 10 },
              stepSize: 20
            }
          }
        }
      }
    });
  }

  initAlcoholChart() {
    const canvas = document.getElementById("alcoholChartCanvas");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, 160);
    gradient.addColorStop(0, "rgba(234, 88, 12, 0.35)");
    gradient.addColorStop(1, "rgba(234, 88, 12, 0.0)");

    this.alcoholChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [...this.timeLabels],
        datasets: [
          {
            label: "Alcohol ADC Reading",
            data: [...this.alcoholHistory],
            borderColor: "#0D9488",
            borderWidth: 2.2,
            backgroundColor: gradient,
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBackgroundColor: "#0D9488"
          },
          {
            label: "Danger Threshold (600 ADC)",
            data: new Array(this.maxDataPoints).fill(600),
            borderColor: "rgba(225, 29, 72, 0.65)",
            borderWidth: 1.5,
            borderDash: [5, 5],
            fill: false,
            pointRadius: 0
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        plugins: {
          legend: {
            display: true,
            labels: {
              color: "#6B7280",
              font: { size: 10 },
              boxWidth: 12
            }
          },
          tooltip: {
            mode: "index",
            intersect: false,
            backgroundColor: "#111827",
            titleColor: "#0D9488",
            bodyColor: "#FFFFFF",
            borderColor: "rgba(234, 88, 12, 0.4)",
            borderWidth: 1,
            callbacks: {
              label: (ctx) => {
                if (ctx.datasetIndex === 0) {
                  return `Alcohol: ${ctx.parsed.y} ADC`;
                }
                return `Threshold: ${ctx.parsed.y} ADC`;
              }
            }
          }
        },
        scales: {
          x: {
            display: true,
            grid: { color: "rgba(17, 24, 39, 0.08)" },
            ticks: {
              color: "#6B7280",
              font: { size: 10 },
              maxTicksLimit: 6
            }
          },
          y: {
            display: true,
            min: 0,
            max: 1024,
            grid: { color: "rgba(17, 24, 39, 0.08)" },
            ticks: {
              color: "#6B7280",
              font: { size: 10 },
              stepSize: 200
            }
          }
        }
      }
    });
  }

  update(speed, alcohol, timestampStr) {
    if (this.isPaused) return;

    const label = timestampStr ? timestampStr.split(" ")[1] || timestampStr : new Date().toTimeString().split(" ")[0];

    // Push new and shift oldest
    this.speedHistory.push(speed);
    if (this.speedHistory.length > this.maxDataPoints) {
      this.speedHistory.shift();
    }

    this.alcoholHistory.push(alcohol);
    if (this.alcoholHistory.length > this.maxDataPoints) {
      this.alcoholHistory.shift();
    }

    this.timeLabels.push(label);
    if (this.timeLabels.length > this.maxDataPoints) {
      this.timeLabels.shift();
    }

    // Refresh Speed Chart
    if (this.speedChart) {
      this.speedChart.data.labels = this.timeLabels;
      this.speedChart.data.datasets[0].data = this.speedHistory;
      this.speedChart.update("none");
    }

    // Refresh Alcohol Chart
    if (this.alcoholChart) {
      this.alcoholChart.data.labels = this.timeLabels;
      this.alcoholChart.data.datasets[0].data = this.alcoholHistory;
      this.alcoholChart.data.datasets[1].data = new Array(this.timeLabels.length).fill(600);
      this.alcoholChart.update("none");
    }
  }

  togglePause() {
    this.isPaused = !this.isPaused;
    return this.isPaused;
  }
}

// Global instance
window.ChartsManager = null;

