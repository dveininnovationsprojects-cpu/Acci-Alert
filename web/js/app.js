/**
 * AcciAlert - Main Application Logic & Dashboard Controller
 * 
 * Handles:
 * - Webcam lifecycle (Start, Stop, Switch Camera)
 * - Real-time Face Detection & Recognition processing loop
 * - Real-time Automotive HUD Drawing on Canvas
 * - Vehicle Ignition & Lock state transitions
 * - Interactive Driver Enrolment Wizard
 * - Security & Authorisation Audit Log with CSV/JSON Export
 * - Hardware Serial & Mock Bridge
 * - Instant Demo Simulation Presets
 */

document.addEventListener('DOMContentLoaded', () => {
    // -------------------------------------------------------------
    // State Initialization
    // -------------------------------------------------------------
    const state = {
        isCameraRunning: false,
        videoStream: null,
        activeTab: 'hud',
        vehicleStatus: 'LOCKED', // 'LOCKED', 'UNLOCKED', 'SCANNING', 'WARNING'
        currentDriver: 'Unknown',
        currentDistance: 99.0,
        currentSimilarity: 0,
        livenessStatus: 'AWAITING_CAMERA',
        consecutiveMatches: 0,
        lastRecognisedCandidate: null,
        enrolledDrivers: {},
        auditLogs: [],
        enrolmentSession: null,
        simulationMode: false,
        logFilter: 'ALL'
    };

    // Load Enrolled Drivers from LocalStorage or Fallback to Defaults
    const savedDrivers = localStorage.getItem('accialert_enrolled_drivers');
    if (savedDrivers) {
        try {
            state.enrolledDrivers = JSON.parse(savedDrivers);
        } catch (e) {
            state.enrolledDrivers = Object.assign({}, DEFAULT_ENROLLED_DRIVERS);
        }
    } else if (typeof DEFAULT_ENROLLED_DRIVERS !== 'undefined') {
        state.enrolledDrivers = Object.assign({}, DEFAULT_ENROLLED_DRIVERS);
        localStorage.setItem('accialert_enrolled_drivers', JSON.stringify(state.enrolledDrivers));
    }

    // Load Audit Logs from LocalStorage
    const savedLogs = localStorage.getItem('accialert_audit_logs');
    if (savedLogs) {
        try {
            state.auditLogs = JSON.parse(savedLogs);
        } catch (e) {
            state.auditLogs = [];
        }
    }

    // -------------------------------------------------------------
    // Core Engines
    // -------------------------------------------------------------
    const faceEngine = new FaceEngine();
    const serialController = new SerialController(
        (logEntry) => appendSerialLog(logEntry),
        (connected, mode) => updateSerialStatusUI(connected, mode)
    );

    // -------------------------------------------------------------
    // DOM Elements
    // -------------------------------------------------------------
    const video = document.getElementById('driverVideo');
    const canvas = document.getElementById('hudCanvas');
    const ctx = canvas.getContext('2d');
    
    // Buttons
    const btnStartCamera = document.getElementById('btnStartCamera');
    const btnStopCamera = document.getElementById('btnStopCamera');
    const btnConnectSerial = document.getElementById('btnConnectSerial');
    const btnResetSystem = document.getElementById('btnResetSystem');
    const btnClearLogs = document.getElementById('btnClearLogs');
    const btnExportCSV = document.getElementById('btnExportCSV');
    const btnExportJSON = document.getElementById('btnExportJSON');
    const btnStartEnrol = document.getElementById('btnStartEnrol');
    const btnResetDrivers = document.getElementById('btnResetDrivers');
    
    // UI Badges & Indicators
    const vehicleStateBadge = document.getElementById('vehicleStateBadge');
    const ignitionDial = document.getElementById('ignitionDial');
    const ignitionIcon = document.getElementById('ignitionIcon');
    const ignitionStatusText = document.getElementById('ignitionStatusText');
    const driverNameDisplay = document.getElementById('driverNameDisplay');
    const similarityScoreDisplay = document.getElementById('similarityScoreDisplay');
    const distanceMetricDisplay = document.getElementById('distanceMetricDisplay');
    const livenessBadge = document.getElementById('livenessBadge');
    const livenessProgressBar = document.getElementById('livenessProgressBar');
    const livenessCard = document.getElementById('livenessCard');
    const livenessHeroBanner = document.getElementById('livenessHeroBanner');
    const livenessHeroIcon = document.getElementById('livenessHeroIcon');
    const livenessHeroText = document.getElementById('livenessHeroText');
    const livenessHeroSub = document.getElementById('livenessHeroSub');
    const livenessBlinkStatus = document.getElementById('livenessBlinkStatus');
    const livenessBlinkProgress = document.getElementById('livenessBlinkProgress');
    const livenessMotionValue = document.getElementById('livenessMotionValue');
    const livenessMotionProgress = document.getElementById('livenessMotionProgress');
    const serialStatusBadge = document.getElementById('serialStatusBadge');
    const serialConsole = document.getElementById('serialConsole');
    const logTableBody = document.getElementById('logTableBody');
    const enrolledDriversList = document.getElementById('enrolledDriversList');
    const enrolledCountBadge = document.getElementById('enrolledCountBadge');

    // Audit Metrics & Filter Elements
    const statTotalEvents = document.getElementById('statTotalEvents');
    const statAuthCount = document.getElementById('statAuthCount');
    const statDeniedCount = document.getElementById('statDeniedCount');
    const statLastTime = document.getElementById('statLastTime');
    const logCounterDisplay = document.getElementById('logCounterDisplay');

    // Checklist Elements & Quick Enrol
    const checkDriverStatus = document.getElementById('checkDriverStatus');
    const checkLivenessStatus = document.getElementById('checkLivenessStatus');
    const checkDecisionStatus = document.getElementById('checkDecisionStatus');
    const btnQuickEnrolMe = document.getElementById('btnQuickEnrolMe');
    const btnQuickAuthorizeDemo = document.getElementById('btnQuickAuthorizeDemo');

    // Threshold Sliders & Settings
    const thresholdSlider = document.getElementById('thresholdSlider');
    const thresholdValueLabel = document.getElementById('thresholdValueLabel');
    const toggleLiveness = document.getElementById('toggleLiveness');
    const demoSelect = document.getElementById('demoSelect');

    // -------------------------------------------------------------
    // Initialize UI
    // -------------------------------------------------------------
    renderEnrolledDrivers();
    renderLogsTable();
    updateVehicleStatusUI('LOCKED', 'Unknown', 0, 99.0, 'SYSTEM READY - CAMERA OFF');

    if (thresholdSlider) {
        thresholdSlider.value = faceEngine.matchThreshold;
        thresholdValueLabel.textContent = faceEngine.matchThreshold.toFixed(1);
        thresholdSlider.addEventListener('input', (e) => {
            faceEngine.matchThreshold = parseFloat(e.target.value);
            thresholdValueLabel.textContent = faceEngine.matchThreshold.toFixed(1);
        });
    }

    if (toggleLiveness) {
        toggleLiveness.addEventListener('change', (e) => {
            faceEngine.livenessConfig.enabled = e.target.checked;
            appendSerialLog({
                timestamp: new Date().toLocaleTimeString(),
                message: `Liveness Anti-Spoofing Check ${e.target.checked ? 'ENABLED' : 'DISABLED'}.`,
                type: 'info'
            });
        });
    }

    // -------------------------------------------------------------
    // Navigation Tabs
    // -------------------------------------------------------------
    function switchTab(targetId) {
        document.querySelectorAll('.nav-tab').forEach(t => {
            t.classList.remove('active');
            if (t.dataset.target === targetId) {
                t.classList.add('active');
            }
        });
        document.querySelectorAll('.tab-content').forEach(c => {
            c.classList.remove('active');
        });
        const targetPane = document.getElementById(targetId);
        if (targetPane) {
            targetPane.classList.add('active');
            state.activeTab = targetId;

            if (targetId === 'tab-enrol') {
                renderEnrolledDrivers();
            } else if (targetId === 'tab-logs') {
                renderLogsTable();
            }
        }
    }

    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = tab.dataset.target;
            if (targetId) {
                switchTab(targetId);
            }
        });
    });

    // -------------------------------------------------------------
    // Camera Control Logic
    // -------------------------------------------------------------
    btnStartCamera.addEventListener('click', startDriverCamera);
    btnStopCamera.addEventListener('click', stopDriverCamera);

    async function startDriverCamera() {
        if (state.isCameraRunning) return;
        try {
            btnStartCamera.disabled = true;
            btnStartCamera.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Initialising...';
            
            const constraints = {
                video: {
                    width: { ideal: 640 },
                    height: { ideal: 480 },
                    facingMode: 'user'
                },
                audio: false
            };

            const stream = await navigator.mediaDevices.getUserMedia(constraints);
            state.videoStream = stream;
            video.srcObject = stream;
            
            await new Promise((resolve) => {
                video.onloadedmetadata = () => {
                    video.play();
                    resolve();
                };
            });

            // Match Canvas dimensions to video stream
            canvas.width = video.videoWidth || 640;
            canvas.height = video.videoHeight || 480;

            state.isCameraRunning = true;
            btnStartCamera.style.display = 'none';
            btnStopCamera.style.display = 'inline-flex';
            btnStartCamera.disabled = false;
            btnStartCamera.innerHTML = '<i class="fas fa-camera"></i> Start Face Detector';

            faceEngine.reset();
            updateVehicleStatusUI('SCANNING', 'Scanning...', 0, 99.0, 'ACTIVE SCANNING');
            
            appendSerialLog({
                timestamp: new Date().toLocaleTimeString(),
                message: 'Webcam feed active. Face detector loop started.',
                type: 'info'
            });

            // Start detection requestAnimationFrame loop
            requestAnimationFrame(detectionLoop);
        } catch (err) {
            btnStartCamera.disabled = false;
            btnStartCamera.innerHTML = '<i class="fas fa-camera"></i> Start Face Detector';
            console.error('Camera access error:', err);
            alert(`Unable to open webcam: ${err.message}\n\nPlease allow camera access or use the "Demo Simulator" dropdown to test.`);
            appendSerialLog({
                timestamp: new Date().toLocaleTimeString(),
                message: `Webcam Error: ${err.message}. You can use Demo Simulator to test.`,
                type: 'error'
            });
        }
    }

    function stopDriverCamera() {
        if (state.videoStream) {
            state.videoStream.getTracks().forEach(track => track.stop());
            state.videoStream = null;
        }
        video.srcObject = null;
        state.isCameraRunning = false;
        btnStartCamera.style.display = 'inline-flex';
        btnStopCamera.style.display = 'none';
        
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Reset state & vehicle lock
        updateVehicleStatusUI('LOCKED', 'Unknown', 0, 99.0, 'CAMERA STOPPED');
        serialController.sendAuth(false, 'Unknown');
        appendAuditLog('CAMERA_STOP', 'INFO', 'Unknown', 0, 'Camera halted, vehicle safely locked');
        faceEngine.reset();
    }

    // -------------------------------------------------------------
    // Core Face Detection & Recognition Processing Loop
    // -------------------------------------------------------------
    let lastProcessTime = 0;
    const PROCESS_INTERVAL_MS = 65; // ~15 FPS processing for responsive UI and low CPU

    async function detectionLoop(timestamp) {
        if (!state.isCameraRunning) return;

        if (timestamp - lastProcessTime > PROCESS_INTERVAL_MS) {
            lastProcessTime = timestamp;
            await processCurrentFrame();
        }

        requestAnimationFrame(detectionLoop);
    }

    async function processCurrentFrame() {
        if (!video.videoWidth || !video.videoHeight) return;

        // Clear previous HUD drawings
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // 1. Detect faces in current frame
        const detectedFaces = await faceEngine.detectFaces(video);

        // Case A: No face detected
        if (!detectedFaces || detectedFaces.length === 0) {
            drawNoFaceHUD();
            state.consecutiveMatches = 0;
            state.lastRecognisedCandidate = null;
            if (state.vehicleStatus !== 'SCANNING' && state.vehicleStatus !== 'LOCKED') {
                updateVehicleStatusUI('SCANNING', 'No Face Detected', 0, 99.0, 'SCANNING');
            }
            return;
        }

        // Case B: Multiple faces detected (Security Hazard)
        if (detectedFaces.length > 1) {
            drawMultipleFacesHUD(detectedFaces);
            state.consecutiveMatches = 0;
            state.lastRecognisedCandidate = null;
            if (state.vehicleStatus !== 'WARNING') {
                updateVehicleStatusUI('WARNING', 'Multiple Faces', 0, 99.0, 'REJECTED - MULTIPLE FACES');
                serialController.sendAuth(false, 'Unknown');
                appendAuditLog('MULTIPLE_FACES', 'HIGH', 'Multiple Faces', 0, 'Multiple individuals detected simultaneously');
            }
            return;
        }

        // Case C: Exactly one face detected
        const faceBox = detectedFaces[0];
        
        // Extract 1024-D LBP histogram & equalized gray patch
        const { descriptor, grayFace } = faceEngine.extractLBPDescriptor(video, faceBox);

        // If currently in an interactive driver enrolment session, collect sample
        if (state.enrolmentSession && state.enrolmentSession.active) {
            handleEnrolmentCapture(descriptor, grayFace, faceBox);
            drawEnrolmentHUD(faceBox);
            return;
        }

        // Run Anti-Spoofing Liveness Evaluation
        const liveness = faceEngine.updateLiveness(grayFace, faceBox);
        updateLivenessUI(liveness);

        // Run Face Recognition against enrolled driver profiles
        const matchResult = faceEngine.matchDriver(descriptor, state.enrolledDrivers);
        
        // Draw HUD targeting bracket and telemetry
        drawTargetHUD(faceBox, matchResult, liveness);

        // Evaluate Authorisation Decision Logic
        evaluateAuthDecision(matchResult, liveness);
    }

    // -------------------------------------------------------------
    // Authorisation Decision Logic & Gating
    // -------------------------------------------------------------
    function evaluateAuthDecision(match, liveness) {
        const candidateName = match.driver;
        const isAuthorised = match.isAuthorised;
        const livenessPassed = liveness.passed;

        if (isAuthorised) {
            if (state.lastRecognisedCandidate === candidateName) {
                state.consecutiveMatches++;
            } else {
                state.lastRecognisedCandidate = candidateName;
                state.consecutiveMatches = 1;
            }

            // Require debounce frames for temporal stability
            if (state.consecutiveMatches >= faceEngine.debounceCount) {
                if (livenessPassed) {
                    if (state.vehicleStatus !== 'UNLOCKED' || state.currentDriver !== candidateName) {
                        // UNLOCK VEHICLE
                        updateVehicleStatusUI('UNLOCKED', candidateName, match.similarity, match.distance, 'AUTHORISED');
                        serialController.sendAuth(true, candidateName);
                        appendAuditLog('AUTH_OK', 'INFO', candidateName, match.similarity, `Driver recognised (${match.similarity}%, dist: ${match.distance})`);
                    }
                } else {
                    // Face matched, awaiting anti-spoofing blink/motion
                    updateVehicleStatusUI('VERIFYING', candidateName, match.similarity, match.distance, 'AWAITING LIVENESS');
                }
            }
        } else {
            // Unknown driver detected
            state.consecutiveMatches = 0;
            state.lastRecognisedCandidate = null;
            
            if (state.vehicleStatus !== 'LOCKED' || state.currentDriver !== 'Unknown') {
                updateVehicleStatusUI('LOCKED', 'Unknown Driver', match.similarity, match.distance, 'UNAUTHORISED - LOCKED');
                serialController.sendAuth(false, 'Unknown');
                appendAuditLog('UNAUTHORISED', 'HIGH', 'Unknown', match.similarity, `Unregistered face detected (dist: ${match.distance} > threshold)`);
            }
        }
    }

    // -------------------------------------------------------------
    // HUD Canvas Drawing Functions (High-Visibility Cockpit Overlay)
    // -------------------------------------------------------------
    function drawTargetHUD(rawBox, match, liveness) {
        // Map raw video coordinates to display canvas coordinates to match mirrored video (scaleX(-1))
        const x = canvas.width - (rawBox.x + rawBox.width);
        const y = rawBox.y;
        const width = rawBox.width;
        const height = rawBox.height;

        const isAuth = match.isAuthorised && liveness.passed;
        const isPending = match.isAuthorised && !liveness.passed;
        
        const mainColor = isAuth ? '#00ff88' : (isPending ? '#ffaa00' : '#ff3344');
        const secondaryColor = 'rgba(10, 16, 28, 0.85)';

        // 1. Futuristic corner brackets
        const cornerLen = Math.min(width, height) * 0.22;
        ctx.strokeStyle = mainColor;
        ctx.lineWidth = 3.5;
        ctx.shadowColor = mainColor;
        ctx.shadowBlur = 8;

        // Top-left
        ctx.beginPath();
        ctx.moveTo(x, y + cornerLen);
        ctx.lineTo(x, y);
        ctx.lineTo(x + cornerLen, y);
        ctx.stroke();

        // Top-right
        ctx.beginPath();
        ctx.moveTo(x + width - cornerLen, y);
        ctx.lineTo(x + width, y);
        ctx.lineTo(x + width, y + cornerLen);
        ctx.stroke();

        // Bottom-left
        ctx.beginPath();
        ctx.moveTo(x, y + height - cornerLen);
        ctx.lineTo(x, y + height);
        ctx.lineTo(x + cornerLen, y + height);
        ctx.stroke();

        // Bottom-right
        ctx.beginPath();
        ctx.moveTo(x + width - cornerLen, y + height);
        ctx.lineTo(x + width, y + height);
        ctx.lineTo(x + width, y + height - cornerLen);
        ctx.stroke();

        ctx.shadowBlur = 0;

        // 2. Animated scanning radar line across face
        const scanY = y + (Math.sin(Date.now() / 250) * 0.5 + 0.5) * height;
        const grad = ctx.createLinearGradient(x, scanY, x + width, scanY);
        grad.addColorStop(0, 'rgba(0,0,0,0)');
        grad.addColorStop(0.5, mainColor);
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.strokeStyle = grad;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x, scanY);
        ctx.lineTo(x + width, scanY);
        ctx.stroke();

        // 3. Eye-tracking telemetry reticles on the driver's eyes
        const eyeY = y + height * 0.35;
        const leftEyeX = x + width * 0.30;
        const rightEyeX = x + width * 0.70;
        const eyeR = Math.max(9, width * 0.09);

        const eyeColor = liveness.passed ? '#00ff88' : (liveness.blinkDetected ? '#00f0ff' : '#00e5ff');
        ctx.strokeStyle = eyeColor;
        ctx.lineWidth = 1.5;

        // Left eye box
        ctx.strokeRect(leftEyeX - eyeR, eyeY - eyeR, eyeR * 2, eyeR * 2);
        // Right eye box
        ctx.strokeRect(rightEyeX - eyeR, eyeY - eyeR, eyeR * 2, eyeR * 2);

        // Micro-target crosshairs on eyes
        ctx.beginPath();
        ctx.moveTo(leftEyeX - 3, eyeY); ctx.lineTo(leftEyeX + 3, eyeY);
        ctx.moveTo(leftEyeX, eyeY - 3); ctx.lineTo(leftEyeX, eyeY + 3);
        ctx.moveTo(rightEyeX - 3, eyeY); ctx.lineTo(rightEyeX + 3, eyeY);
        ctx.moveTo(rightEyeX, eyeY - 3); ctx.lineTo(rightEyeX, eyeY + 3);
        ctx.stroke();

        ctx.font = 'bold 9px "Segoe UI", monospace';
        ctx.fillStyle = eyeColor;
        ctx.fillText(liveness.blinkDetected ? 'EYE: BLINK OK' : 'EYE TRACK ACTIVE', x + width * 0.28, eyeY + eyeR + 13);

        // 4. Driver identity tag (rendered above or below face to avoid clipping at screen edge)
        const tagText = match.isAuthorised ? `DRIVER: ${match.bestMatchName}` : 'UNKNOWN DRIVER';
        const subText = `${match.similarity}% Match | Dist: ${match.distance} | Liveness: ${liveness.passed ? 'VERIFIED (REAL HUMAN)' : liveness.status}`;
        
        ctx.font = 'bold 13px "Segoe UI", sans-serif';
        const tagW = Math.max(width, ctx.measureText(subText).width + 24);
        const tagH = 44;
        const tagY = (y > tagH + 15) ? (y - tagH - 4) : (y + height + 8);
        
        ctx.fillStyle = secondaryColor;
        ctx.fillRect(x, tagY, tagW, tagH);
        
        ctx.fillStyle = mainColor;
        ctx.fillRect(x, tagY, 4, tagH);

        ctx.fillStyle = '#ffffff';
        ctx.fillText(tagText, x + 12, tagY + 20);

        ctx.font = '11px "Segoe UI", sans-serif';
        ctx.fillStyle = mainColor;
        ctx.fillText(subText, x + 12, tagY + 36);

        // 5. High-Visibility HUD Top Liveness Ribbon
        drawLivenessTopHUDBanner(liveness);
    }

    function drawLivenessTopHUDBanner(liveness) {
        const bannerW = Math.min(500, canvas.width - 40);
        const bannerH = 34;
        const bannerX = (canvas.width - bannerW) / 2;
        const bannerY = 14;

        const isLive = liveness && liveness.passed;
        const isBlink = liveness && liveness.blinkDetected;
        const strokeColor = isLive ? '#00ff88' : (isBlink ? '#00f0ff' : '#ffaa00');
        const fillColor = 'rgba(10, 16, 28, 0.90)';

        ctx.save();
        ctx.fillStyle = fillColor;
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 2;
        ctx.shadowColor = strokeColor;
        ctx.shadowBlur = 10;

        // Rounded pill box
        const r = 8;
        ctx.beginPath();
        ctx.moveTo(bannerX + r, bannerY);
        ctx.lineTo(bannerX + bannerW - r, bannerY);
        ctx.quadraticCurveTo(bannerX + bannerW, bannerY, bannerX + bannerW, bannerY + r);
        ctx.lineTo(bannerX + bannerW, bannerY + bannerH - r);
        ctx.quadraticCurveTo(bannerX + bannerW, bannerY + bannerH, bannerX + bannerW - r, bannerY + bannerH);
        ctx.lineTo(bannerX + r, bannerY + bannerH);
        ctx.quadraticCurveTo(bannerX, bannerY + bannerH, bannerX, bannerY + bannerH - r);
        ctx.lineTo(bannerX, bannerY + r);
        ctx.quadraticCurveTo(bannerX, bannerY, bannerX + r, bannerY);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();

        // Pulsing indicator dot
        ctx.beginPath();
        ctx.arc(bannerX + 18, bannerY + bannerH / 2, 5, 0, Math.PI * 2);
        ctx.fillStyle = strokeColor;
        ctx.fill();

        ctx.shadowBlur = 0;
        ctx.font = 'bold 12px "Segoe UI", sans-serif';
        ctx.fillStyle = '#ffffff';

        if (isLive) {
            ctx.fillText('🛡️ ANTI-SPOOFING LIVENESS: VERIFIED (LIVE HUMAN)', bannerX + 32, bannerY + 22);
            ctx.fillStyle = '#00ff88';
            ctx.font = 'bold 11px "Segoe UI", monospace';
            ctx.fillText('BLINK: OK | VITALITY: OK', bannerX + bannerW - 175, bannerY + 22);
        } else if (isBlink) {
            ctx.fillText('👁️ LIVENESS: BLINK DETECTED (CONFIRMING MOTION)', bannerX + 32, bannerY + 22);
            ctx.fillStyle = '#00f0ff';
            ctx.font = 'bold 11px "Segoe UI", monospace';
            ctx.fillText('ANALYZING...', bannerX + bannerW - 110, bannerY + 22);
        } else {
            ctx.fillText('⏳ LIVENESS: ANALYZING EYE BLINK & MICRO-MOTION...', bannerX + 32, bannerY + 22);
            ctx.fillStyle = '#ffaa00';
            ctx.font = 'bold 11px "Segoe UI", monospace';
            ctx.fillText('ACTIVE', bannerX + bannerW - 65, bannerY + 22);
        }
        ctx.restore();
    }

    function drawNoFaceHUD() {
        ctx.font = 'bold 15px "Segoe UI", sans-serif';
        ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
        ctx.textAlign = 'center';
        ctx.fillText('SCANNING DRIVER SEAT POSITION... (NO DRIVER DETECTED)', canvas.width / 2, 45);
        ctx.textAlign = 'left';
    }

    function drawMultipleFacesHUD(faces) {
        ctx.strokeStyle = '#ff2233';
        ctx.lineWidth = 3;
        for (const f of faces) {
            const fx = canvas.width - (f.x + f.width);
            ctx.strokeRect(fx, f.y, f.width, f.height);
        }
        ctx.fillStyle = 'rgba(255, 34, 51, 0.85)';
        ctx.fillRect(0, 0, canvas.width, 42);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 15px "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('SECURITY WARNING: MULTIPLE FACES DETECTED - VEHICLE LOCKED', canvas.width / 2, 26);
        ctx.textAlign = 'left';
    }

    function drawEnrolmentHUD(rawBox) {
        const x = canvas.width - (rawBox.x + rawBox.width);
        const y = rawBox.y;
        const width = rawBox.width;
        const height = rawBox.height;

        ctx.strokeStyle = '#00e5ff';
        ctx.lineWidth = 4;
        ctx.strokeRect(x, y, width, height);

        const session = state.enrolmentSession;
        const percent = Math.round((session.samples.length / session.targetSamples) * 100);

        ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
        ctx.fillRect(x, y + height + 8, width, 32);

        ctx.fillStyle = '#00e5ff';
        ctx.font = 'bold 13px "Segoe UI", sans-serif';
        ctx.fillText(`Enrolling ${session.driverName}: ${percent}% (${session.samples.length}/${session.targetSamples})`, x + 10, y + height + 28);
    }

    // -------------------------------------------------------------
    // UI State Updating Functions
    // -------------------------------------------------------------
    function updateVehicleStatusUI(vStatus, driverName, similarity, distance, bannerMsg) {
        state.vehicleStatus = vStatus;
        state.currentDriver = driverName;
        state.currentSimilarity = similarity;
        state.currentDistance = distance;

        driverNameDisplay.textContent = driverName;
        similarityScoreDisplay.textContent = `${similarity}%`;
        distanceMetricDisplay.textContent = distance.toFixed(2);

        const isDriverEnrolled = (driverName !== 'Unknown' && driverName !== 'Scanning...' && driverName !== 'No Face Detected' && driverName !== 'Multiple Faces');
        const isLive = faceEngine.livenessState.passed;

        if (checkDriverStatus) {
            if (isDriverEnrolled) {
                checkDriverStatus.className = 'badge bg-success';
                checkDriverStatus.innerHTML = `<i class="fas fa-check"></i> ${driverName}`;
            } else {
                checkDriverStatus.className = 'badge bg-danger';
                checkDriverStatus.innerHTML = `<i class="fas fa-times"></i> Unknown`;
            }
        }

        if (checkLivenessStatus) {
            if (isLive) {
                checkLivenessStatus.className = 'badge bg-success';
                checkLivenessStatus.innerHTML = `<i class="fas fa-check"></i> LIVE HUMAN`;
            } else if (faceEngine.livenessState.blinkDetected) {
                checkLivenessStatus.className = 'badge bg-info';
                checkLivenessStatus.innerHTML = `<i class="fas fa-eye"></i> Blink OK`;
            } else {
                checkLivenessStatus.className = 'badge bg-warning text-dark';
                checkLivenessStatus.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Checking...`;
            }
        }

        if (vStatus === 'UNLOCKED') {
            vehicleStateBadge.className = 'status-badge badge-unlocked';
            vehicleStateBadge.innerHTML = '<i class="fas fa-unlock"></i> VEHICLE UNLOCKED';
            if (ignitionDial) ignitionDial.className = 'ignition-dial unlocked';
            if (ignitionIcon) ignitionIcon.className = 'fas fa-power-off';
            ignitionStatusText.textContent = 'ENGINE START AUTHORISED';
            ignitionStatusText.className = 'ignition-main-banner text-success fw-bold';
            if (checkDecisionStatus) {
                checkDecisionStatus.className = 'badge bg-success';
                checkDecisionStatus.innerHTML = '<i class="fas fa-unlock"></i> UNLOCKED (Active)';
            }
        } else if (vStatus === 'VERIFYING') {
            vehicleStateBadge.className = 'status-badge badge-verifying';
            vehicleStateBadge.innerHTML = '<i class="fas fa-user-check"></i> VERIFYING LIVENESS';
            if (ignitionDial) ignitionDial.className = 'ignition-dial locked';
            if (ignitionIcon) ignitionIcon.className = 'fas fa-shield-alt text-warning';
            ignitionStatusText.textContent = 'AWAITING BLINK / MOTION VERIFICATION';
            ignitionStatusText.className = 'ignition-main-banner text-warning fw-bold';
            if (checkDecisionStatus) {
                checkDecisionStatus.className = 'badge bg-warning text-dark';
                checkDecisionStatus.innerHTML = 'PENDING (Liveness)';
            }
        } else if (vStatus === 'WARNING') {
            vehicleStateBadge.className = 'status-badge badge-locked';
            vehicleStateBadge.innerHTML = '<i class="fas fa-exclamation-triangle"></i> SECURITY LOCK';
            if (ignitionDial) ignitionDial.className = 'ignition-dial locked shake-red';
            if (ignitionIcon) ignitionIcon.className = 'fas fa-lock text-danger';
            ignitionStatusText.textContent = 'LOCKED (MULTIPLE FACES DETECTED)';
            ignitionStatusText.className = 'ignition-main-banner text-danger fw-bold';
            if (checkDecisionStatus) {
                checkDecisionStatus.className = 'badge bg-danger';
                checkDecisionStatus.innerHTML = 'LOCKED (Multi-Face)';
            }
        } else if (vStatus === 'SCANNING') {
            vehicleStateBadge.className = 'status-badge badge-scanning';
            vehicleStateBadge.innerHTML = '<i class="fas fa-satellite-dish"></i> SCANNING';
            if (ignitionDial) ignitionDial.className = 'ignition-dial locked';
            if (ignitionIcon) ignitionIcon.className = 'fas fa-lock text-secondary';
            ignitionStatusText.textContent = 'VEHICLE LOCKED - SCANNING DRIVER';
            ignitionStatusText.className = 'ignition-main-banner text-secondary fw-bold';
            if (checkDecisionStatus) {
                checkDecisionStatus.className = 'badge bg-secondary';
                checkDecisionStatus.innerHTML = 'LOCKED (No Driver)';
            }
        } else {
            // LOCKED
            vehicleStateBadge.className = 'status-badge badge-locked';
            vehicleStateBadge.innerHTML = '<i class="fas fa-lock"></i> VEHICLE LOCKED';
            if (ignitionDial) ignitionDial.className = 'ignition-dial locked';
            if (ignitionIcon) ignitionIcon.className = 'fas fa-lock text-danger';
            ignitionStatusText.textContent = isLive 
                ? 'VEHICLE LOCKED: LIVE HUMAN BUT UNREGISTERED DRIVER'
                : 'ENGINE DISABLED - UNAUTHORISED DRIVER';
            ignitionStatusText.className = 'ignition-main-banner text-danger fw-bold';
            if (checkDecisionStatus) {
                checkDecisionStatus.className = 'badge bg-danger';
                checkDecisionStatus.innerHTML = 'LOCKED (Unknown Driver)';
            }
        }
    }

    function updateLivenessUI(liveness) {
        if (!faceEngine.livenessConfig.enabled) {
            if (livenessBadge) {
                livenessBadge.className = 'badge bg-secondary text-white';
                livenessBadge.textContent = 'BYPASSED';
            }
            if (livenessProgressBar) {
                livenessProgressBar.style.width = '100%';
                livenessProgressBar.className = 'progress-bar bg-secondary';
            }
            return;
        }

        if (liveness.passed) {
            if (livenessBadge) {
                livenessBadge.className = 'badge bg-success text-white';
                livenessBadge.innerHTML = '<i class="fas fa-check"></i> VERIFIED';
            }
            if (livenessProgressBar) {
                livenessProgressBar.style.width = '100%';
                livenessProgressBar.className = 'progress-bar bg-success';
            }
        } else if (liveness.blinkDetected) {
            if (livenessBadge) {
                livenessBadge.className = 'badge bg-info text-dark';
                livenessBadge.innerHTML = '<i class="fas fa-eye"></i> BLINK OK';
            }
            if (livenessProgressBar) {
                livenessProgressBar.style.width = '70%';
                livenessProgressBar.className = 'progress-bar bg-info';
            }
        } else {
            if (livenessBadge) {
                livenessBadge.className = 'badge bg-warning text-dark';
                livenessBadge.innerHTML = '<i class="fas fa-spinner fa-spin"></i> CHECKING';
            }
            if (livenessProgressBar) {
                livenessProgressBar.style.width = '35%';
                livenessProgressBar.className = 'progress-bar bg-warning';
            }
        }
    }

    function updateSerialStatusUI(connected, mode) {
        if (connected) {
            serialStatusBadge.className = 'badge bg-success';
            serialStatusBadge.innerHTML = '<i class="fas fa-plug"></i> ESP32 USB CONNECTED (115200)';
            btnConnectSerial.innerHTML = '<i class="fas fa-unlink"></i> Disconnect ESP32';
            btnConnectSerial.className = 'btn btn-outline-danger btn-sm';
        } else {
            serialStatusBadge.className = 'badge bg-warning text-dark';
            serialStatusBadge.innerHTML = '<i class="fas fa-microchip"></i> VIRTUAL SERIAL (MOCK BRIDGE)';
            btnConnectSerial.innerHTML = '<i class="fas fa-plug"></i> Connect ESP32 Hardware';
            btnConnectSerial.className = 'btn btn-outline-info btn-sm';
        }
    }

    function appendSerialLog(logEntry) {
        const line = document.createElement('div');
        line.className = `terminal-line terminal-${logEntry.type}`;
        line.innerHTML = `<span class="terminal-ts">[${logEntry.timestamp}]</span> ${escapeHTML(logEntry.message)}`;
        serialConsole.appendChild(line);
        serialConsole.scrollTop = serialConsole.scrollHeight;

        // Keep maximum 100 terminal lines
        while (serialConsole.children.length > 100) {
            serialConsole.removeChild(serialConsole.firstChild);
        }
    }

    // -------------------------------------------------------------
    // Audit & Event Logging
    // -------------------------------------------------------------
    function appendAuditLog(type, severity, driver, similarity, details) {
        const logItem = {
            id: state.auditLogs.length + 1,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            type: type,
            severity: severity,
            driver: driver,
            similarity: similarity,
            liveness: faceEngine.livenessConfig.enabled ? (faceEngine.livenessState.passed ? 'PASSED' : 'PENDING') : 'BYPASSED',
            details: details,
            espCommand: `{"cmd":"AUTH","ok":${type === 'AUTH_OK' ? 1 : 0},"driver":"${driver}"}`
        };

        state.auditLogs.unshift(logItem);
        // Limit storage to 200 events
        if (state.auditLogs.length > 200) state.auditLogs.pop();
        localStorage.setItem('accialert_audit_logs', JSON.stringify(state.auditLogs));
        renderLogsTable();
    }

    function renderLogsTable() {
        if (!logTableBody) return;
        
        // Calculate telemetry metrics for the top summary strip
        const total = state.auditLogs.length;
        const authCount = state.auditLogs.filter(l => l.type === 'AUTH_OK').length;
        const deniedCount = state.auditLogs.filter(l => l.type === 'UNAUTHORISED' || l.type === 'MULTIPLE_FACES').length;
        const lastTime = total > 0 ? (state.auditLogs[0].timestamp.split(' ')[1] || state.auditLogs[0].timestamp) : '--:--:--';

        if (statTotalEvents) statTotalEvents.textContent = total;
        if (statAuthCount) statAuthCount.textContent = authCount;
        if (statDeniedCount) statDeniedCount.textContent = deniedCount;
        if (statLastTime) statLastTime.textContent = lastTime;

        // Apply filters
        let filtered = state.auditLogs;
        if (state.logFilter === 'AUTH_OK') {
            filtered = state.auditLogs.filter(l => l.type === 'AUTH_OK');
        } else if (state.logFilter === 'UNAUTHORISED') {
            filtered = state.auditLogs.filter(l => l.type === 'UNAUTHORISED' || l.type === 'MULTIPLE_FACES');
        }

        if (logCounterDisplay) {
            logCounterDisplay.textContent = `Showing ${filtered.length} of ${total} events`;
        }

        logTableBody.innerHTML = '';

        if (filtered.length === 0) {
            logTableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4"><i class="fas fa-inbox fa-2x mb-2 d-block"></i>No matching authentication events recorded.</td></tr>';
            return;
        }

        for (const item of filtered) {
            const tr = document.createElement('tr');
            
            // Format timestamp into date & time
            const parts = item.timestamp.split(' ');
            const datePart = parts[0] || '';
            const timePart = parts[1] || item.timestamp;

            // Badges
            let eventBadge = '';
            if (item.type === 'AUTH_OK') {
                eventBadge = '<span class="badge-cockpit auth-ok"><i class="fas fa-check-circle"></i> AUTH OK</span>';
            } else if (item.type === 'UNAUTHORISED') {
                eventBadge = '<span class="badge-cockpit unauth"><i class="fas fa-shield-alt"></i> UNAUTHORISED</span>';
            } else if (item.type === 'MULTIPLE_FACES') {
                eventBadge = '<span class="badge-cockpit unauth"><i class="fas fa-users-slash"></i> MULTI-FACE</span>';
            } else {
                eventBadge = `<span class="badge-cockpit system-event"><i class="fas fa-info-circle"></i> ${item.type}</span>`;
            }

            const sevBadge = item.severity === 'HIGH' 
                ? '<span class="badge-severity-high"><i class="fas fa-exclamation-triangle me-1"></i>HIGH</span>'
                : '<span class="badge-severity-info">INFO</span>';

            const isSuccess = item.similarity >= 60;
            const progressColor = isSuccess ? '#00ff88' : '#ff3355';

            tr.innerHTML = `
                <td>
                    <div class="audit-ts-wrapper">
                        <span class="audit-ts-time">${timePart}</span>
                        <span class="audit-ts-date">${datePart}</span>
                    </div>
                </td>
                <td>${eventBadge}</td>
                <td>${sevBadge}</td>
                <td>
                    <div class="audit-driver-pill">
                        <div class="audit-driver-avatar"><i class="fas fa-user"></i></div>
                        <span>${escapeHTML(item.driver)}</span>
                    </div>
                </td>
                <td>
                    <div class="audit-match-box">
                        <span class="fw-bold ${isSuccess ? 'text-success' : 'text-danger'}">${item.similarity}%</span>
                        <div class="audit-match-bar">
                            <div class="audit-match-fill" style="width: ${Math.min(100, item.similarity)}%; background-color: ${progressColor};"></div>
                        </div>
                    </div>
                </td>
                <td>
                    <span class="badge ${item.liveness === 'PASSED' ? 'bg-success' : 'bg-secondary'}">
                        <i class="fas fa-shield-alt me-1"></i> ${item.liveness}
                    </span>
                </td>
                <td>
                    <code class="audit-json-code" title="${escapeHTML(item.espCommand)}">${escapeHTML(item.espCommand)}</code>
                </td>
            `;
            logTableBody.appendChild(tr);
        }
    }

    // -------------------------------------------------------------
    // Audit Log Filters & Actions
    // -------------------------------------------------------------
    document.querySelectorAll('.log-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.log-filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.logFilter = btn.dataset.filter;
            renderLogsTable();
        });
    });

    btnClearLogs.addEventListener('click', () => {
        if (confirm('Clear all stored authentication audit logs?')) {
            state.auditLogs = [];
            localStorage.removeItem('accialert_audit_logs');
            renderLogsTable();
        }
    });

    btnExportCSV.addEventListener('click', () => {
        if (state.auditLogs.length === 0) {
            alert('No logs to export.');
            return;
        }
        let csv = 'ID,Timestamp,Type,Severity,Driver,Similarity,Liveness,Details,ESP32_Command\n';
        for (const log of state.auditLogs) {
            csv += `"${log.id}","${log.timestamp}","${log.type}","${log.severity}","${log.driver}","${log.similarity}","${log.liveness}","${log.details.replace(/"/g, '""')}","${log.espCommand.replace(/"/g, '""')}"\n`;
        }
        downloadFile(csv, 'accialert_audit_logs.csv', 'text/csv');
    });

    btnExportJSON.addEventListener('click', () => {
        if (state.auditLogs.length === 0) {
            alert('No logs to export.');
            return;
        }
        const jsonStr = JSON.stringify(state.auditLogs, null, 2);
        downloadFile(jsonStr, 'accialert_audit_logs.json', 'application/json');
    });

    // -------------------------------------------------------------
    // Driver Enrolment Wizard
    // -------------------------------------------------------------
    btnStartEnrol.addEventListener('click', () => {
        const nameInput = document.getElementById('enrolDriverName');
        const roleInput = document.getElementById('enrolDriverRole');
        const driverName = (nameInput.value || '').trim();
        const driverRole = (roleInput.value || 'Authorised Driver').trim();

        if (!driverName) {
            alert('Please enter a valid driver name.');
            nameInput.focus();
            return;
        }

        if (!state.isCameraRunning) {
            alert('Please start the webcam first before starting face enrolment.');
            return;
        }

        // Start Enrolment Session
        state.enrolmentSession = {
            active: true,
            driverName: driverName,
            driverRole: driverRole,
            targetSamples: 15,
            samples: [],
            thumbnails: [],
            startTime: Date.now()
        };

        btnStartEnrol.disabled = true;
        btnStartEnrol.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Enrolling...';
        appendSerialLog({
            timestamp: new Date().toLocaleTimeString(),
            message: `Starting enrolment for driver "${driverName}". Please hold position and follow camera guidance.`,
            type: 'info'
        });
    });

    function handleEnrolmentCapture(descriptor, grayFace, faceBox) {
        const session = state.enrolmentSession;
        session.samples.push(descriptor);

        // Store first sample as thumbnail avatar
        if (session.thumbnails.length === 0) {
            const thumbCanvas = document.createElement('canvas');
            thumbCanvas.width = 80;
            thumbCanvas.height = 80;
            const tCtx = thumbCanvas.getContext('2d');
            tCtx.drawImage(video, faceBox.x, faceBox.y, faceBox.width, faceBox.height, 0, 0, 80, 80);
            session.thumbnails.push(thumbCanvas.toDataURL('image/jpeg', 0.85));
        }

        // Update progress in enrolment UI
        const percent = Math.round((session.samples.length / session.targetSamples) * 100);
        const enrolProgress = document.getElementById('enrolProgress');
        if (enrolProgress) {
            enrolProgress.style.width = `${percent}%`;
            enrolProgress.textContent = `${percent}%`;
        }

        if (session.samples.length >= session.targetSamples) {
            // Compute average descriptor across captured samples
            const avgDescriptor = new Float32Array(faceEngine.descriptorLength);
            for (const sample of session.samples) {
                for (let i = 0; i < faceEngine.descriptorLength; i++) {
                    avgDescriptor[i] += sample[i];
                }
            }
            for (let i = 0; i < faceEngine.descriptorLength; i++) {
                avgDescriptor[i] /= session.samples.length;
            }

            // Save new driver profile
            state.enrolledDrivers[session.driverName] = {
                name: session.driverName,
                role: session.driverRole,
                date: new Date().toISOString().substring(0, 10),
                samples: session.samples.length,
                thumbnail: session.thumbnails[0] || '',
                descriptor: Array.from(avgDescriptor)
            };

            localStorage.setItem('accialert_enrolled_drivers', JSON.stringify(state.enrolledDrivers));
            
            appendSerialLog({
                timestamp: new Date().toLocaleTimeString(),
                message: `Enrolment completed successfully for driver "${session.driverName}" (${session.samples.length} samples). Profile stored.`,
                type: 'success'
            });

            appendAuditLog('ENROL_SUCCESS', 'INFO', session.driverName, 100, `New driver enrolled with ${session.samples.length} feature samples`);

            // Reset enrolment session
            state.enrolmentSession = null;
            btnStartEnrol.disabled = false;
            btnStartEnrol.innerHTML = '<i class="fas fa-user-plus"></i> Enrol Driver';
            if (enrolProgress) {
                enrolProgress.style.width = '0%';
                enrolProgress.textContent = '0%';
            }
            document.getElementById('enrolDriverName').value = '';

            renderEnrolledDrivers();
            alert(`Driver "${session.driverName}" has been successfully enrolled! The AI will now recognise this driver.`);
        }
    }

    function renderEnrolledDrivers() {
        if (!enrolledDriversList) return;
        enrolledDriversList.innerHTML = '';
        const driverKeys = Object.keys(state.enrolledDrivers);
        if (enrolledCountBadge) enrolledCountBadge.textContent = driverKeys.length;

        if (driverKeys.length === 0) {
            enrolledDriversList.innerHTML = '<div class="col-12 text-center text-muted py-3">No drivers enrolled yet. Click "Enrol New Driver" to register.</div>';
            return;
        }

        for (const [key, profile] of Object.entries(state.enrolledDrivers)) {
            const card = document.createElement('div');
            card.className = 'col-md-4 col-sm-6 mb-3';
            const thumbSrc = profile.thumbnail || 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect width="80" height="80" fill="%23222"/><text x="40" y="45" font-size="20" fill="%23888" text-anchor="middle">👤</text></svg>';
            
            card.innerHTML = `
                <div class="card driver-card h-100 bg-dark text-light border-secondary">
                    <div class="card-body d-flex align-items-center">
                        <img src="${thumbSrc}" class="rounded-circle me-3 border border-info" style="width: 54px; height: 54px; object-fit: cover;">
                        <div class="flex-grow-1">
                            <h6 class="mb-0 text-info fw-bold">${escapeHTML(profile.name)}</h6>
                            <small class="text-muted d-block">${escapeHTML(profile.role || 'Driver')}</small>
                            <small class="text-secondary"><i class="fas fa-database"></i> ${profile.samples || 20} samples</small>
                        </div>
                        <button class="btn btn-sm btn-outline-danger btn-delete-driver" data-name="${escapeHTML(profile.name)}" title="Delete Driver">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
            `;
            enrolledDriversList.appendChild(card);
        }

        // Attach delete listener
        document.querySelectorAll('.btn-delete-driver').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const nameToDelete = e.currentTarget.dataset.name;
                if (confirm(`Remove driver "${nameToDelete}" from registered authorisation database?`)) {
                    delete state.enrolledDrivers[nameToDelete];
                    localStorage.setItem('accialert_enrolled_drivers', JSON.stringify(state.enrolledDrivers));
                    renderEnrolledDrivers();
                    appendSerialLog({
                        timestamp: new Date().toLocaleTimeString(),
                        message: `Driver "${nameToDelete}" deleted from registry.`,
                        type: 'warning'
                    });
                }
            });
        });
    }

    btnResetDrivers.addEventListener('click', () => {
        if (confirm('Reset driver registry back to factory pre-enrolled drivers (Arun, Kumar, Ravi)?')) {
            state.enrolledDrivers = Object.assign({}, DEFAULT_ENROLLED_DRIVERS);
            localStorage.setItem('accialert_enrolled_drivers', JSON.stringify(state.enrolledDrivers));
            renderEnrolledDrivers();
            appendSerialLog({
                timestamp: new Date().toLocaleTimeString(),
                message: 'Enrolled drivers reset to factory defaults (Arun, Kumar, Ravi).',
                type: 'info'
            });
        }
    });

    // -------------------------------------------------------------
    // ESP32 Serial Hardware Connection
    // -------------------------------------------------------------
    btnConnectSerial.addEventListener('click', async () => {
        if (serialController.isConnected) {
            await serialController.disconnect();
        } else {
            await serialController.connect();
        }
    });

    // -------------------------------------------------------------
    // System Reset
    // -------------------------------------------------------------
    btnResetSystem.addEventListener('click', () => {
        faceEngine.reset();
        state.consecutiveMatches = 0;
        state.lastRecognisedCandidate = null;
        updateVehicleStatusUI('LOCKED', 'Unknown', 0, 99.0, 'SYSTEM RESET - VEHICLE LOCKED');
        serialController.sendAuth(false, 'Unknown');
        appendAuditLog('SYSTEM_RESET', 'INFO', 'System', 0, 'Driver authorisation reset; vehicle locked');
        appendSerialLog({
            timestamp: new Date().toLocaleTimeString(),
            message: 'Manual system reset triggered. Vehicle ignition locked immediately.',
            type: 'warning'
        });
    });

    // -------------------------------------------------------------
    // Demo Simulator Presets
    // -------------------------------------------------------------
    if (demoSelect && typeof DEMO_TEST_SUBJECTS !== 'undefined') {
        demoSelect.addEventListener('change', async (e) => {
            const key = e.target.value;
            if (!key) return;

            const demoData = DEMO_TEST_SUBJECTS[key];
            if (!demoData) return;

            appendSerialLog({
                timestamp: new Date().toLocaleTimeString(),
                message: `[DEMO SIMULATOR] Running test preset for subject: "${key}"`,
                type: 'info'
            });

            // Create temporary image element and process
            const demoImg = new Image();
            demoImg.src = demoData.image;
            demoImg.onload = async () => {
                // Draw onto canvas
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(demoImg, 0, 0, canvas.width, canvas.height);

                // Run detection on demo image
                const detectedFaces = await faceEngine.detectFaces(demoImg);
                if (detectedFaces && detectedFaces.length > 0) {
                    const box = detectedFaces[0];
                    const { descriptor, grayFace } = faceEngine.extractLBPDescriptor(demoImg, box);
                    
                    // In demo mode simulate liveness as passed for real subjects
                    const liveness = {
                        passed: demoData.type === 'authorised',
                        status: demoData.type === 'authorised' ? 'VERIFIED' : 'FAILED',
                        motionScore: 2.5,
                        blinkDetected: true
                    };
                    updateLivenessUI(liveness);

                    const matchResult = faceEngine.matchDriver(descriptor, state.enrolledDrivers);
                    drawTargetHUD(box, matchResult, liveness);

                    if (demoData.type === 'authorised' && matchResult.isAuthorised) {
                        updateVehicleStatusUI('UNLOCKED', matchResult.driver, matchResult.similarity, matchResult.distance, 'AUTHORISED (DEMO)');
                        serialController.sendAuth(true, matchResult.driver);
                        appendAuditLog('AUTH_OK', 'INFO', matchResult.driver, matchResult.similarity, `Demo test: Authorised driver ${matchResult.driver}`);
                    } else {
                        updateVehicleStatusUI('LOCKED', 'Unknown Driver', matchResult.similarity, matchResult.distance, 'UNAUTHORISED (DEMO)');
                        serialController.sendAuth(false, 'Unknown');
                        appendAuditLog('UNAUTHORISED', 'HIGH', 'Unknown', matchResult.similarity, `Demo test: Unknown driver rejected`);
                    }
                }
            };
        });
    }

    // -------------------------------------------------------------
    // Helper Utilities
    // -------------------------------------------------------------
    function escapeHTML(str) {
        return (str || '').replace(/[&<>'"]/g, tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag));
    }

    function downloadFile(content, fileName, mimeType) {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }
});

