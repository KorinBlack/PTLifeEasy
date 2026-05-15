new Vue({
    el: '#wsdl-app',
    delimiters: ['[[', ']]'],
    data: {
        wsdlUrl: '',
        loading: false,
        error: null,
        actions: [],
        targetNamespace: '',
        payloadsRaw: "0\n-1\n' OR 1=1--\n\" OR 1=1--\n<script>alert(1)</script>\n../../../../etc/passwd",
        attacking: false,
        progress: 0,
        globalResults: []
    },
    computed: {
        payloadList() {
            return this.payloadsRaw.split('\n').map(p => p.trim()).filter(p => p !== '');
        },
        payloadCount() {
            return this.payloadList.length;
        }
    },
    methods: {
        async parseWsdl() {
            if (!this.wsdlUrl) {
                this.error = "ERROR: Provide a valid WSDL URL.";
                return;
            }
            this.loading = true;
            this.error = null;
            this.actions = [];
            this.globalResults = [];

            try {
                const res = await fetch('/wsdl/api/parse', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: this.wsdlUrl })
                });
                const data = await res.json();
                
                if (!res.ok) throw new Error(data.error || 'Unknown error');
                
                // Add expanded and fieldResults state for UI
                this.actions = data.actions.map(a => ({
                    ...a,
                    expanded: false,
                    fieldResults: []
                }));
                this.targetNamespace = data.target_namespace;
                
            } catch (err) {
                this.error = "PARSE_ERROR: " + err.message;
            } finally {
                this.loading = false;
            }
        },
        toggleAction(index) {
            this.actions[index].expanded = !this.actions[index].expanded;
        },
        async testSingleAction(action) {
            // A quick test with a single payload (default values)
            this.error = null;
            try {
                const res = await fetch('/wsdl/api/attack_field', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.wsdlUrl,
                        action: action,
                        target_field: action.fields.length > 0 ? action.fields[0].name : '',
                        payloads: ['?'], // Dummy payload just to run the default values
                        target_namespace: this.targetNamespace
                    })
                });
                const data = await res.json();
                action.fieldResults = data.results;
                this.$forceUpdate();
            } catch (err) {
                alert("EXECUTION_ERROR: " + err.message);
            }
        },
        async attackField(action, field) {
            if (this.payloadCount === 0) {
                alert("ERROR: No payloads loaded.");
                return;
            }
            this.attacking = true;
            this.progress = 0;
            action.fieldResults = [];
            
            try {
                // To simulate a progress bar and not timeout the browser, we should ideally chunk requests or use WebSockets.
                // For now, we will send all payloads in one request, but we simulate progress.
                // In a real pentest tool, the backend should stream results.
                const res = await fetch('/wsdl/api/attack_field', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.wsdlUrl,
                        action: action,
                        target_field: field.name,
                        payloads: this.payloadList,
                        target_namespace: this.targetNamespace
                    })
                });
                
                this.progress = 100;
                const data = await res.json();
                action.fieldResults = data.results;
                this.$forceUpdate();
                
            } catch (err) {
                alert("ATTACK_ERROR: " + err.message);
            } finally {
                this.attacking = false;
            }
        },
        async runCollection() {
            if (this.payloadCount === 0) {
                alert("ERROR: No payloads loaded.");
                return;
            }
            this.attacking = true;
            this.progress = 0;
            this.globalResults = [];
            
            try {
                // Send the entire matrix to the backend
                // This could be heavy, but we'll simulate progress
                const interval = setInterval(() => {
                    if (this.progress < 90) this.progress += 5;
                }, 500);

                const res = await fetch('/wsdl/api/run_collection', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.wsdlUrl,
                        actions: this.actions,
                        payloads: this.payloadList,
                        target_namespace: this.targetNamespace
                    })
                });
                
                clearInterval(interval);
                this.progress = 100;
                
                const data = await res.json();
                this.globalResults = data.results;
                
            } catch (err) {
                alert("MATRIX_ERROR: " + err.message);
            } finally {
                setTimeout(() => { this.attacking = false; }, 1000);
            }
        },
        getStatusClass(status) {
            if (status === 200) return 'status-ok';
            if (status >= 400 && status < 500) return 'status-warn';
            return 'status-err';
        }
    }
});
