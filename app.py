from flask import Flask, render_template


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("base.html")

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)

