"""
Vercel and WSGI Entrypoint for AeroCast-TH
Exports top-level app, application, and handler variables.
"""
from server import app, application, handler, wsgi_app

if __name__ == "__main__":
    import server
    server.main() if hasattr(server, 'main') else None
