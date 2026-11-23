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
        globalResults: [],
        customHeaders: '',
        resultsExpanded: true,
        resultsFilter: '',
        sortKey: '',
        sortAsc: true,
        // Power Edit
        powerEditExpanded: true,
        powerEditFields: [],
        powerEditPattern: '',
        powerEditPatternValue: '',
        _lastPowerEditMsg: null
    },
    computed: {
        payloadList() {
            return this.payloadsRaw.split('\n').map(p => p.trim()).filter(p => p !== '');
        },
        payloadCount() {
            return this.payloadList.length;
        },
        allActive() {
            if (this.actions.length === 0) return false;
            // Only check if actions are active, ignore fields
            return this.actions.every(a => a.active);
        },
        powerEditAffectedActions() {
            // Count unique actions that have at least one power edit field
            const affected = new Set();
            this.powerEditFields.forEach(pef => {
                pef.locations.forEach(loc => affected.add(loc.actionName));
            });
            return affected.size;
        },
        filteredAndSortedResults() {
            let data = this.globalResults;
            
            if (this.resultsFilter) {
                const term = this.resultsFilter.toLowerCase();
                data = data.filter(r => 
                    String(r.action_name).toLowerCase().includes(term) ||
                    String(r.field_name).toLowerCase().includes(term) ||
                    String(r.payload).toLowerCase().includes(term) ||
                    String(r.status).includes(term) ||
                    String(r.length).includes(term) ||
                    String(r.time_ms).includes(term)
                );
            }
            
            if (this.sortKey) {
                data = data.slice().sort((a, b) => {
                    let aVal = a[this.sortKey];
                    let bVal = b[this.sortKey];
                    
                    if (typeof aVal === 'string') {
                        aVal = aVal.toLowerCase();
                        bVal = typeof bVal === 'string' ? bVal.toLowerCase() : bVal;
                    }
                    
                    if (aVal < bVal) return this.sortAsc ? -1 : 1;
                    if (aVal > bVal) return this.sortAsc ? 1 : -1;
                    return 0;
                });
            }
            
            return data;
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
                    body: JSON.stringify({ url: this.wsdlUrl, headers: this.customHeaders })
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
                // Auto-detect common fields for Power Edit
                this.refreshPowerEdit();
                
            } catch (err) {
                this.error = "PARSE_ERROR: " + err.message;
            } finally {
                this.loading = false;
            }
        },
        toggleAction(index) {
            this.actions[index].expanded = !this.actions[index].expanded;
        },
        toggleAll() {
            const targetState = !this.allActive;
            this.actions.forEach(a => {
                a.active = targetState;
            });
        },
        toggleAllFields(action) {
            const allFieldsActive = action.fields.length > 0 && action.fields.every(f => f.active);
            const targetState = !allFieldsActive;
            action.fields.forEach(f => f.active = targetState);
        },
        async testSingleAction(action) {
            // A quick test with a single payload (default values)
            this.error = null;
            try {
                const res = await fetch('/wsdl/api/play_request', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.wsdlUrl,
                        action: action,
                        target_namespace: this.targetNamespace,
                        headers: this.customHeaders
                    })
                });
                const data = await res.json();
                // Ensure fieldResults is always an array
                const results = data.results;
                action.fieldResults = Array.isArray(results) ? results : [results];
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
                        target_namespace: this.targetNamespace,
                        headers: this.customHeaders
                    })
                });
                
                this.progress = 100;
                const data = await res.json();
                action.fieldResults = Array.isArray(data.results) ? data.results : [data.results];
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
            
            let interval;
            try {
                // Send the entire matrix to the backend
                // This could be heavy, but we'll simulate progress
                interval = setInterval(() => {
                    if (this.progress < 90) this.progress += 5;
                }, 500);

                const res = await fetch('/wsdl/api/run_collection', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.wsdlUrl,
                        actions: this.actions,
                        payloads: this.payloadList,
                        target_namespace: this.targetNamespace,
                        headers: this.customHeaders
                    })
                });
                
                clearInterval(interval);
                this.progress = 100;
                
                const data = await res.json();
                this.globalResults = Array.isArray(data.results) ? data.results : [];
                
            } catch (err) {
                clearInterval(interval);
                alert("MATRIX_ERROR: " + err.message);
            } finally {
                setTimeout(() => { this.attacking = false; }, 1000);
            }
        },
        async testXXE(action) {
            if (this.payloadCount === 0) {
                alert("ERROR: No payloads loaded. Add XXE payloads.");
                return;
            }
            this.attacking = true;
            action.fieldResults = [];
            
            try {
                const res = await fetch('/wsdl/api/attack_xxe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.wsdlUrl,
                        action: action,
                        payloads: this.payloadList,
                        target_namespace: this.targetNamespace,
                        headers: this.customHeaders
                    })
                });
                const data = await res.json();
                action.fieldResults = Array.isArray(data.results) ? data.results : [data.results];
                this.$forceUpdate();
            } catch(err) {
                alert("XXE_ERROR: " + err.message);
            } finally {
                this.attacking = false;
            }
        },
        getStatusClass(status) {
            if (status === 200) return 'status-ok';
            if (status >= 400 && status < 500) return 'status-warn';
            return 'status-err';
        },
        sortBy(key) {
            if (this.sortKey === key) {
                this.sortAsc = !this.sortAsc;
            } else {
                this.sortKey = key;
                this.sortAsc = true;
            }
        },
        exportResults(format) {
            const data = this.filteredAndSortedResults;
            if (data.length === 0) {
                alert("No results to export.");
                return;
            }
            
            let content = '';
            let type = '';
            let filename = `wsdl_fuzzer_results.${format}`;
            
            if (format === 'json') {
                content = JSON.stringify(data, null, 2);
                type = 'application/json';
            } else if (format === 'csv') {
                const headers = ['action_name', 'field_name', 'payload', 'status', 'length', 'time_ms', 'error'];
                content = headers.join(',') + '\n';
                data.forEach(row => {
                    const rowData = headers.map(h => {
                        let val = row[h] !== null && row[h] !== undefined ? String(row[h]) : '';
                        val = val.replace(/"/g, '""');
                        if (val.includes(',') || val.includes('"') || val.includes('\n')) {
                            val = `"${val}"`;
                        }
                        return val;
                    });
                    content += rowData.join(',') + '\n';
                });
                type = 'text/csv';
            }
            
            const blob = new Blob([content], { type: type });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        },
        clearGlobalResults() {
            this.globalResults = [];
        },
        saveState() {
            const state = {
                wsdlUrl: this.wsdlUrl,
                customHeaders: this.customHeaders,
                payloadsRaw: this.payloadsRaw,
                actions: this.actions,
                targetNamespace: this.targetNamespace
            };
            const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = 'wsdl_fuzzer_state.json';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        },
        loadState(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const state = JSON.parse(e.target.result);
                    if (state.wsdlUrl !== undefined) this.wsdlUrl = state.wsdlUrl;
                    if (state.customHeaders !== undefined) this.customHeaders = state.customHeaders;
                    if (state.payloadsRaw !== undefined) this.payloadsRaw = state.payloadsRaw;
                    if (state.actions !== undefined) this.actions = state.actions;
                    if (state.targetNamespace !== undefined) this.targetNamespace = state.targetNamespace;
                    
                    // Refresh power edit after loading state
                    this.refreshPowerEdit();
                    alert("State loaded successfully!");
                } catch (err) {
                    alert("ERROR parsing state file: " + err.message);
                }
            };
            reader.readAsText(file);
            event.target.value = '';
        },
        
        // ==================== POWER EDIT METHODS ====================
        
        refreshPowerEdit() {
            // Detect fields with the same name appearing in multiple actions
            const fieldMap = {};
            
            this.actions.forEach((action, aIndex) => {
                if (!action.fields) return;
                action.fields.forEach((field, fIndex) => {
                    const name = field.name.toLowerCase();
                    if (!fieldMap[name]) {
                        fieldMap[name] = {
                            name: field.name,
                            count: 0,
                            value: '',
                            locations: []
                        };
                    }
                    fieldMap[name].count++;
                    fieldMap[name].locations.push({
                        actionIndex: aIndex,
                        fieldIndex: fIndex,
                        actionName: action.name
                    });
                });
            });
            
            // Keep only fields that appear in 2+ actions, sorted by count desc
            this.powerEditFields = Object.values(fieldMap)
                .filter(f => f.count >= 2)
                .sort((a, b) => b.count - a.count);
        },
        
        applyPowerEditSingle(pef) {
            if (!pef.value.trim()) return;
            
            let applied = 0;
            pef.locations.forEach(loc => {
                const action = this.actions[loc.actionIndex];
                if (action && action.fields && action.fields[loc.fieldIndex]) {
                    action.fields[loc.fieldIndex].value = pef.value;
                    applied++;
                }
            });
            
            this.$forceUpdate();
            // Flash feedback
            const msg = `Applied "${pef.value}" to ${applied} field(s) named "${pef.name}"`;
            this._flashMessage(msg);
        },
        
        applyPowerEditAll() {
            let totalApplied = 0;
            this.powerEditFields.forEach(pef => {
                if (!pef.value.trim()) return;
                pef.locations.forEach(loc => {
                    const action = this.actions[loc.actionIndex];
                    if (action && action.fields && action.fields[loc.fieldIndex]) {
                        action.fields[loc.fieldIndex].value = pef.value;
                        totalApplied++;
                    }
                });
            });
            
            this.$forceUpdate();
            this._flashMessage(`Power Edit: Applied values to ${totalApplied} field(s) across all actions.`);
        },
        
        clearPowerEditValues() {
            this.powerEditFields.forEach(pef => { pef.value = ''; });
            this.powerEditPattern = '';
            this.powerEditPatternValue = '';
        },
        
        applyPowerEditPattern() {
            if (!this.powerEditPattern.trim() || !this.powerEditPatternValue.trim()) return;
            
            let regex;
            try {
                regex = new RegExp(this.powerEditPattern, 'i');
            } catch (e) {
                alert("Invalid regex pattern: " + e.message);
                return;
            }
            
            let applied = 0;
            this.actions.forEach(action => {
                if (!action.fields) return;
                action.fields.forEach(field => {
                    if (regex.test(field.name)) {
                        field.value = this.powerEditPatternValue;
                        applied++;
                    }
                });
            });
            
            this.$forceUpdate();
            // Refresh the auto-detected list since values may have changed
            this.refreshPowerEdit();
            this._flashMessage(`Pattern "${this.powerEditPattern}" matched ${applied} field(s). Value set to "${this.powerEditPatternValue}".`);
        },
        
        _flashMessage(msg) {
            // Simple flash message (could be improved with a toast component)
            console.log('[POWER_EDIT]', msg);
            // Store last message for display
            this._lastPowerEditMsg = msg;
            setTimeout(() => { this._lastPowerEditMsg = null; }, 3000);
        }
    }
});
