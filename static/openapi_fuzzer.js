new Vue({
    el: '#openapi-app',
    delimiters: ['[[', ']]'],
    data: {
        specUrl: '',
        loading: false,
        error: null,
        actions: [],
        baseUrl: '',
        originalBaseUrl: '',
        openapiVersion: '',
        collectionTitle: '',
        payloadsRaw: "0\n-1\n' OR 1=1--\n\" OR 1=1--\n<script>alert(1)</script>\n../../../../etc/passwd\n{ \"$ne\": null }\ntrue\nfalse",
        attacking: false,
        progress: 0,
        globalResults: [],
        customHeaders: '',
        auth: { type: 'none', username: '', password: '', token: '', key_name: 'X-API-Key', key_value: '' },
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
            return this.actions.every(a => a.active);
        },
        powerEditAffectedActions() {
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
        async parseSpec() {
            if (!this.specUrl) {
                this.error = "ERROR: Provide a valid OpenAPI/Swagger URL.";
                return;
            }
            this.loading = true;
            this.error = null;
            this.actions = [];
            this.globalResults = [];

            try {
                const res = await fetch('/openapi/api/parse', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: this.specUrl, headers: this.customHeaders })
                });
                const data = await res.json();
                
                if (!res.ok) throw new Error(data.error || 'Unknown error');
                
                this.actions = data.actions.map(a => ({
                    ...a,
                    expanded: false,
                    fieldResults: [],
                    override_base_url: false,
                    custom_base_url: '',
                    override_auth: false,
                    auth_config: { type: 'none', username: '', password: '', token: '', key_name: 'X-API-Key', key_value: '' }
                }));
                this.baseUrl = data.base_url;
                this.originalBaseUrl = data.base_url;
                this.openapiVersion = data.openapi_version;
                this.collectionTitle = data.title;
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
            this.error = null;
            try {
                const fieldValues = {};
                action.fields.forEach(f => {
                    if (f.active) fieldValues[f.name] = f.value;
                });
                const res = await fetch('/openapi/api/play_request', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.specUrl,
                        action: action,
                        field_values: fieldValues,
                        headers: this.customHeaders,
                        base_url: this.baseUrl,
                        auth: this.auth
                    })
                });
                const data = await res.json();
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
                const res = await fetch('/openapi/api/attack_field', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.specUrl,
                        action: action,
                        target_field: field.name,
                        payloads: this.payloadList,
                        headers: this.customHeaders,
                        base_url: this.baseUrl,
                        auth: this.auth
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
                interval = setInterval(() => {
                    if (this.progress < 90) this.progress += 5;
                }, 500);

                const res = await fetch('/openapi/api/run_collection', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.specUrl,
                        actions: this.actions,
                        payloads: this.payloadList,
                        headers: this.customHeaders,
                        base_url: this.baseUrl,
                        auth: this.auth
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
            let filename = `openapi_fuzzer_results.${format}`;
            
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
        resetBaseUrl() {
            this.baseUrl = this.originalBaseUrl;
        },
        saveState() {
            const state = {
                specUrl: this.specUrl,
                customHeaders: this.customHeaders,
                payloadsRaw: this.payloadsRaw,
                actions: this.actions,
                baseUrl: this.baseUrl,
                originalBaseUrl: this.originalBaseUrl,
                openapiVersion: this.openapiVersion,
                collectionTitle: this.collectionTitle,
                auth: this.auth
            };
            const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = 'openapi_fuzzer_state.json';
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
                    if (state.specUrl !== undefined) this.specUrl = state.specUrl;
                    if (state.customHeaders !== undefined) this.customHeaders = state.customHeaders;
                    if (state.payloadsRaw !== undefined) this.payloadsRaw = state.payloadsRaw;
                    if (state.actions !== undefined) this.actions = state.actions;
                    if (state.baseUrl !== undefined) this.baseUrl = state.baseUrl;
                    if (state.originalBaseUrl !== undefined) this.originalBaseUrl = state.originalBaseUrl;
                    if (state.openapiVersion !== undefined) this.openapiVersion = state.openapiVersion;
                    if (state.collectionTitle !== undefined) this.collectionTitle = state.collectionTitle;
                    if (state.auth !== undefined) this.auth = state.auth;
                    
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
            const msg = `Applied "${pef.value}" to ${applied} parameter(s) named "${pef.name}"`;
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
            this._flashMessage(`Power Edit: Applied values to ${totalApplied} parameter(s) across all endpoints.`);
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
            this.refreshPowerEdit();
            this._flashMessage(`Pattern "${this.powerEditPattern}" matched ${applied} parameter(s). Value set to "${this.powerEditPatternValue}".`);
        },
        
        _flashMessage(msg) {
            console.log('[POWER_EDIT]', msg);
            this._lastPowerEditMsg = msg;
            setTimeout(() => { this._lastPowerEditMsg = null; }, 3000);
        }
    }
});