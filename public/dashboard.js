// Dashboard Interactivity
class Dashboard {
    constructor() {
        this.initializeEventListeners();
        this.startAnimations();
    }

    initializeEventListeners() {
        // Navigation items
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            item.addEventListener('click', (e) => {
                navItems.forEach(i => i.classList.remove('active'));
                item.classList.add('active');
            });
        });

        // Keyboard inputs
        const inputs = document.querySelectorAll('.keyboard-inputs input');
        inputs.forEach(input => {
            input.addEventListener('change', (e) => {
                console.log(`Value changed: ${e.target.value}`);
            });
        });

        // Buttons
        const buttons = document.querySelectorAll('.btn');
        buttons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.handleButtonClick(e.target.closest('.btn'));
            });
        });

        // Control buttons
        const controlBtns = document.querySelectorAll('.btn-control, .btn-control-large, .btn-small');
        controlBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.handleControlClick(e.target.closest('button'));
            });
        });

        // Tab buttons
        const tabBtns = document.querySelectorAll('.tab-btn');
        tabBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                tabBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });
    }

    handleButtonClick(btn) {
        const text = btn.textContent.trim();
        console.log(`Button clicked: ${text}`);

        // Add visual feedback
        btn.style.transform = 'scale(0.95)';
        setTimeout(() => {
            btn.style.transform = 'scale(1)';
        }, 100);
    }

    handleControlClick(btn) {
        const text = btn.textContent.trim();
        console.log(`Control clicked: ${text}`);
    }

    startAnimations() {
        // Animate compass needle
        this.animateCompass();

        // Pulse effect on status values
        this.pulseStatusValues();

        // Simulate live data updates
        this.updateLiveData();
    }

    animateCompass() {
        const needle = document.querySelector('.compass-needle');
        if (needle) {
            let rotation = 0;
            setInterval(() => {
                rotation += Math.random() * 10 - 5;
                needle.style.transform = `translateX(-50%) rotate(${rotation}deg)`;
            }, 2000);
        }
    }

    pulseStatusValues() {
        const statusValues = document.querySelectorAll('.status-value');
        statusValues.forEach(value => {
            setInterval(() => {
                value.style.textShadow = '0 0 20px rgba(0, 229, 255, 0.8)';
                setTimeout(() => {
                    value.style.textShadow = '0 0 10px rgba(0, 229, 255, 0.5)';
                }, 500);
            }, 3000);
        });
    }

    updateLiveData() {
        // Simulate depth changes
        const depthCard = document.querySelector('.status-value');
        if (depthCard) {
            // Update would happen here with real data from backend
        }
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const dashboard = new Dashboard();
    console.log('HYDROSHIPS Dashboard initialized');
});
