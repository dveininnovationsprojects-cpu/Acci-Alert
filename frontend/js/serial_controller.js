/**
 * AcciAlert - ESP32 Serial Controller (Web Serial API & Virtual Serial Bridge)
 * 
 * Hardware Contract:
 * - Baud rate: 115200 baud
 * - Format: JSON-line terminated by \n
 * - Authorised:   {"cmd":"AUTH","ok":1,"driver":"Arun"}\n
 * - Unauthorised: {"cmd":"AUTH","ok":0,"driver":"Unknown"}\n
 */

class SerialController {
    constructor(onLogCallback, onStatusCallback) {
        this.port = null;
        this.writer = null;
        this.reader = null;
        this.isConnected = false;
        this.baudRate = 115200;
        this.txCount = 0;
        this.rxCount = 0;
        this.lastCommand = null;
        
        this.onLog = onLogCallback || console.log;
        this.onStatus = onStatusCallback || (() => {});
        
        this.isWebSerialSupported = ('serial' in navigator);
    }

    /**
     * Request user to pick a physical serial port and connect at 115200 baud
     */
    async connect() {
        if (!this.isWebSerialSupported) {
            this.log('Web Serial API is not supported in this browser (Use Chrome or Edge). Running in VIRTUAL SERIAL mode.', 'warning');
            return false;
        }

        try {
            this.port = await navigator.serial.requestPort();
            await this.port.open({ baudRate: this.baudRate });
            
            const textEncoder = new TextEncoderStream();
            textEncoder.readable.pipeTo(this.port.writable);
            this.writer = textEncoder.writable.getWriter();
            
            this.isConnected = true;
            this.log(`Connected to physical ESP32 on USB Serial at ${this.baudRate} baud.`, 'success');
            this.onStatus(true, 'PHYSICAL_CONNECTED');
            
            // Start listening for ESP32 responses asynchronously
            this.listenToESP32();
            return true;
        } catch (err) {
            this.isConnected = false;
            this.log(`Serial connection failed or cancelled: ${err.message}. Operating in VIRTUAL MOCK mode.`, 'warning');
            this.onStatus(false, 'MOCK_MODE');
            return false;
        }
    }

    /**
     * Disconnect physical port cleanly
     */
    async disconnect() {
        if (this.writer) {
            await this.writer.close();
            this.writer = null;
        }
        if (this.port) {
            await this.port.close();
            this.port = null;
        }
        this.isConnected = false;
        this.onStatus(false, 'DISCONNECTED');
        this.log('Disconnected from ESP32 serial port.', 'info');
    }

    /**
     * Send structured JSON driver authorization command to ESP32
     */
    async sendAuth(status, driver) {
        const payload = {
            cmd: "AUTH",
            ok: status ? 1 : 0,
            driver: status ? driver : "Unknown"
        };
        const jsonStr = JSON.stringify(payload) + "\n";
        this.lastCommand = payload;
        this.txCount++;

        if (this.isConnected && this.writer) {
            try {
                await this.writer.write(jsonStr);
                this.log(`[TX -> ESP32] ${jsonStr.trim()}`, 'tx');
            } catch (err) {
                this.log(`[TX Error] Failed to write to serial: ${err.message}`, 'error');
            }
        } else {
            // Emulate transmission in Virtual Serial Monitor
            this.log(`[MOCK TX -> ESP32] ${jsonStr.trim()}`, 'tx');
            
            // Simulate ESP32 microcontroller ACK response after 45ms
            setTimeout(() => {
                const ack = {
                    status: "ACK",
                    ignition: status ? "ENABLED" : "LOCKED",
                    driver: payload.driver,
                    ts: Date.now()
                };
                this.rxCount++;
                this.log(`[MOCK RX <- ESP32] ${JSON.stringify(ack)}`, 'rx');
            }, 45);
        }

        return payload;
    }

    /**
     * Background listener for incoming telemetry from physical ESP32
     */
    async listenToESP32() {
        if (!this.port || !this.port.readable) return;
        try {
            const textDecoder = new TextDecoderStream();
            this.port.readable.pipeTo(textDecoder.writable);
            const reader = textDecoder.readable.getReader();
            
            let buffer = "";
            while (this.isConnected) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += value;
                const lines = buffer.split("\n");
                buffer = lines.pop(); // keep remainder
                for (const line of lines) {
                    if (line.trim().length > 0) {
                        this.rxCount++;
                        this.log(`[RX <- ESP32] ${line.trim()}`, 'rx');
                    }
                }
            }
        } catch (err) {
            this.log(`Serial read loop ended: ${err.message}`, 'warning');
        }
    }

    log(message, type = 'info') {
        if (this.onLog) {
            this.onLog({
                timestamp: new Date().toLocaleTimeString(),
                message,
                type
            });
        }
    }
}

// Expose globally
window.SerialController = SerialController;

