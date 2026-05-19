# PTLifeEasy

**PTLifeEasy** is a highly modular, extensible Flask-based framework designed specifically for penetration testers and security researchers. It provides a centralized, cyberpunk-themed web interface to orchestrate, manage, and execute various custom security tools and workflows from a single unified dashboard.

## 🚀 Features

- **Modular Architecture**: Drop-in new tools easily without touching the core routing logic. The application dynamically loads any Python script or package placed in the `modules/` directory that exposes a Flask `Blueprint`.
- **Global Configuration Management**: Built-in settings dashboard to manage global parameters like HTTP/HTTPS Proxies (e.g., Burp Suite) and Custom Headers (e.g., Authorization tokens, User-Agents). These settings can be imported and utilized by any loaded module.
- **Cyberpunk Aesthetic**: Features a sleek, responsive, and highly customized UI/UX leveraging neon colors, terminal-like fonts, and custom CSS components to create an immersive hacker environment.
- **Auto-Reloading**: Automatically restarts the server when core configurations change.

## 🛠️ Getting Started

### Prerequisites
- Python 3.8+
- pip (Python package installer)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/YourUsername/PTLifeEasy.git
   cd PTLifeEasy
   ```

2. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv .venv
   # On Windows
   .venv\Scripts\activate
   # On Linux/macOS
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python app.py
   ```

5. Open your browser and navigate to: `http://localhost:5000`

## 🧩 Extending PTLifeEasy (Adding Modules)

The true power of PTLifeEasy lies in its extensibility. You can create standalone security tools (like a WSDL Fuzzer, Nmap parser, Directory Brute-forcer, etc.) and integrate them seamlessly.

For a comprehensive guide on creating modules, including the recommended **Class-Based Architecture** for larger projects, please refer to the [Module Creation Guide](MODULE_CREATION_GUIDE.md).

### Quick Module Overview
1. Create `modules/my_tool.py`.
2. Define `MODULE_NAME`, `MODULE_DESCRIPTION`, and `MODULE_PREFIX`.
3. Create a Flask Blueprint named `module_bp`.
4. Define your routes and core logic.

## ⚙️ Global Settings

The built-in Settings page allows you to define global parameters that your modules can leverage via `utils.load_config()`.

- **Proxy Configuration**: Route traffic from your modules through an interception proxy like Burp Suite or OWASP ZAP.
- **Custom Headers**: Define global headers (like `Authorization: Bearer <token>`) that your modules can attach to outgoing requests.

## 🤝 Contributing

Contributions are welcome! If you have developed a cool module or want to improve the core framework, feel free to open a Pull Request.

## 📄 License

This project is intended for authorized penetration testing and educational purposes only.