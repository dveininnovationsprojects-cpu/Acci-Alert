/**
 * AcciAlert - Real-Time Speed & Alcohol Monitoring Charts
 * Rolling 60-second window using Chart.js.
 * Author: Muniyappan
 */

class LiveChartsManager {
  constructor() {
    this.speedChart = null;
    this.alcoholChart = null;
    this.windowSeconds = 60;   // the charts show the last 60 seconds of real readings
    this.maxDataPoints = 300;  // safety limit if readings arrive very fast
    this.alcoholThreshold = 220;   // same as the alcohol alert level in backend/config.py

    this.speedHistory = [];
    this.alcoholHistory = [];
    this.timeLabels = [];
    this.epochs = [];          // when each reading arrived (seconds), used to drop readings older than 60 s

    this.isPaused = false;
    this.initCharts();
  }

  initCharts() {
    // No made-up start values: the charts stay empty until the first real reading arrives
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
            suggestedMax: 100,      // grows if the speed goes above 100
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
            label: `Alert level (${this.alcoholThreshold} ADC)`,
            data: [],
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
            suggestedMax: 400,      // grows up to 1023 if the reading goes higher
            grid: { color: "rgba(17, 24, 39, 0.08)" },
            ticks: {
              color: "#6B7280",
              font: { size: 10 },
              stepSize: 100
            }
          }
        }
      }
    });
  }

  // speed (km/h), alcohol (ADC), timestampStr ("YYYY-MM-DD HH:MM:SS" of the reading), epoch (seconds of the reading)
  update(speed, alcohol, timestampStr, epoch) {
    if (this.isPaused) return;

    const label = timestampStr ? timestampStr.split(" ")[1] || timestampStr : new Date().toTimeString().split(" ")[0];
    const at = typeof epoch === "number" ? epoch : Date.now() / 1000;

    this.speedHistory.push(speed);
    this.alcoholHistory.push(alcohol);
    this.timeLabels.push(label);
    this.epochs.push(at);

    // Keep only the last 60 seconds (by time, not by number of readings)
    while (this.epochs.length > 1 && (this.epochs[0] < at - this.windowSeconds || this.epochs.length > this.maxDataPoints)) {
      this.epochs.shift();
      this.speedHistory.shift();
      this.alcoholHistory.shift();
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
      this.alcoholChart.data.datasets[1].data = new Array(this.timeLabels.length).fill(this.alcoholThreshold);
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

