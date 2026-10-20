
import io
import json
import os
import re
from datetime import datetime

import mysql.connector
import requests
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, jsonify, send_file
)
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer
)
from reportlab.lib.units import inch
from xml.sax.saxutils import escape

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv(
    "FLASK_SECRET_KEY", "development-only-change-me"
)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

OLLAMA_URL = os.getenv(
    "OLLAMA_URL", "http://localhost:11434"
).rstrip("/")
OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL", "llama3.2:1b"
)

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv(
        "MYSQL_DATABASE", "question_paper_db"
    ),
}


def get_db():
    return mysql.connector.connect(**DB_CONFIG)


def init_db():
    connection = get_db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS question_papers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            subject VARCHAR(255) NOT NULL,
            difficulty VARCHAR(30) NOT NULL,
            total_marks INT NOT NULL,
            paper_json LONGTEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    cursor.close()
    connection.close()


def extract_pdf_text(file):
    reader = PdfReader(file)
    pages = []

    for page in reader.pages:
        pages.append(page.extract_text() or "")

    text = "\n".join(pages).strip()

    if not text:
        raise ValueError(
            "No readable text found in this PDF. "
            "Scanned PDFs require OCR."
        )

    # Keep prompts manageable for a small model.
    return text[:12000]


def distribute_marks(total_marks, count):
    """Allocate integer marks that always add up to total_marks."""
    base, remainder = divmod(total_marks, count)

    return [
        base + (1 if i < remainder else 0)
        for i in range(count)
    ]


def generate_with_ollama(subject, syllabus, count,
                         total_marks, difficulty):
    prompt = f"""
You are an examination question paper generator.

Create exactly {count} distinct questions for the subject:
{subject}

Difficulty: {difficulty}
Total paper marks: {total_marks}

Use the supplied syllabus as the source of truth.
Cover different topics fairly. Do not repeat questions.
Avoid questions that require information absent from the syllabus.

Return valid JSON only, in this exact structure:
{{
  "title": "Question Paper",
  "questions": [
    {{
      "question": "Question text",
      "type": "Short Answer",
      "options": [],
      "answer": "Correct answer",
      "explanation": "Brief explanation",
      "topic": "Topic name"
    }}
  ]
}}

Rules:
- Return exactly {count} questions.
- Mix MCQs, short-answer and descriptive questions when suitable.
- For MCQs, provide exactly four options and include the correct answer.
- For non-MCQs, return an empty options array.
- Keep answers and explanations concise.
- Do not include marks; the application assigns marks.
- Do not add markdown fences or text outside the JSON.

SYLLABUS:
{syllabus}
"""

    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.3,
                "num_predict": 3000
            }
        },
        timeout=300
    )
    response.raise_for_status()

    result = response.json()
    paper = json.loads(result["response"])

    questions = paper.get("questions")

    if not isinstance(questions, list):
        raise ValueError("The AI returned an invalid question list.")

    if len(questions) != count:
        raise ValueError(
            f"The AI returned {len(questions)} questions, "
            f"but {count} were requested. Please try again."
        )

    marks = distribute_marks(total_marks, count)
    cleaned_questions = []

    for i, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError("The AI returned an invalid question.")

        question_text = str(question.get("question", "")).strip()

        if not question_text:
            raise ValueError(f"Question {i + 1} is empty.")

        q_type = str(
            question.get("type", "Short Answer")
        ).strip()[:50]

        options = question.get("options", [])
        if not isinstance(options, list):
            options = []

        options = [
            str(option).strip()[:500]
            for option in options[:4]
        ]

        if q_type.lower() == "mcq" and len(options) != 4:
            raise ValueError(
                f"Question {i + 1} is an MCQ but does not "
                "contain exactly four options. Please retry."
            )

        cleaned_questions.append({
            "number": i + 1,
            "question": question_text[:2000],
            "type": q_type,
            "options": options,
            "answer": str(question.get("answer", ""))[:2000],
            "explanation": str(
                question.get("explanation", "")
            )[:2000],
            "topic": str(question.get("topic", ""))[:200],
            "marks": marks[i]
        })

    return {
        "title": str(
            paper.get("title", "Question Paper")
        )[:200],
        "subject": subject,
        "difficulty": difficulty,
        "total_marks": total_marks,
        "questions": cleaned_questions
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    try:
        subject = request.form.get("subject", "").strip()
        syllabus = request.form.get("syllabus", "").strip()
        difficulty = request.form.get("difficulty", "Medium")
        count = int(request.form.get("question_count", "10"))
        total_marks = int(request.form.get("total_marks", "50"))

        if not subject:
            return jsonify(error="Enter a subject name."), 400

        if not 1 <= count <= 30:
            return jsonify(
                error="Choose between 1 and 30 questions."
            ), 400

        if not count <= total_marks <= 300:
            return jsonify(
                error="Total marks must be at least the "
                      "question count and no more than 300."
            ), 400

        if difficulty not in ["Easy", "Medium", "Hard"]:
            return jsonify(error="Invalid difficulty."), 400

        uploaded = request.files.get("syllabus_file")

        if uploaded and uploaded.filename:
            if not uploaded.filename.lower().endswith(".pdf"):
                return jsonify(
                    error="Please upload a PDF file."
                ), 400

            syllabus += "\n" + extract_pdf_text(uploaded.stream)

        syllabus = syllabus.strip()

        if not syllabus:
            return jsonify(
                error="Enter syllabus text or upload a PDF."
            ), 400

        if len(syllabus) > 12000:
            syllabus = syllabus[:12000]

        paper = generate_with_ollama(
            subject, syllabus, count, total_marks, difficulty
        )

        connection = get_db()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO question_papers
                (subject, difficulty, total_marks, paper_json)
            VALUES (%s, %s, %s, %s)
        """, (
            subject,
            difficulty,
            total_marks,
            json.dumps(paper, ensure_ascii=False)
        ))

        paper["id"] = cursor.lastrowid
        connection.commit()
        cursor.close()
        connection.close()

        return jsonify(paper)

    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except requests.RequestException:
        app.logger.exception("Ollama request failed")
        return jsonify(
            error="Could not connect to Ollama. "
                  "Check that Ollama is running and the model "
                  "has been downloaded."
        ), 502
    except mysql.connector.Error:
        app.logger.exception("MySQL operation failed")
        return jsonify(
            error="Database error. Check your MySQL settings "
                  "and confirm the database exists."
        ), 500
    except Exception:
        app.logger.exception("Unexpected generation error")
        return jsonify(
            error="Something went wrong. Check the Flask terminal."
        ), 500


@app.route("/history")
def history():
    try:
        connection = get_db()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, subject, difficulty, total_marks, created_at
            FROM question_papers
            ORDER BY id DESC
            LIMIT 50
        """)

        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        for row in rows:
            if isinstance(row.get("created_at"), datetime):
                row["created_at"] = row["created_at"].strftime(
                    "%Y-%m-%d %H:%M"
                )

        return jsonify(rows)

    except mysql.connector.Error:
        app.logger.exception("Could not load history")
        return jsonify(error="Could not load paper history."), 500


@app.route("/paper/<int:paper_id>")
def get_paper(paper_id):
    try:
        connection = get_db()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT paper_json FROM question_papers WHERE id = %s",
            (paper_id,)
        )
        row = cursor.fetchone()

        cursor.close()
        connection.close()

        if not row:
            return jsonify(error="Paper not found."), 404

        return jsonify(json.loads(row[0]))

    except mysql.connector.Error:
        app.logger.exception("Could not retrieve paper")
        return jsonify(error="Database error."), 500


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    subject = str(data.get("subject", "Question Paper"))[:200]
    questions = data.get("questions", [])

    if not isinstance(questions, list) or not questions:
        return jsonify(error="No questions to export."), 400

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch
    )

    styles = getSampleStyleSheet()
    story = [
        Paragraph(escape(subject), styles["Title"]),
        Spacer(1, 12),
        Paragraph(
            f"Total Marks: {int(data.get('total_marks', 0))}",
            styles["Normal"]
        ),
        Paragraph(
            f"Difficulty: {escape(str(data.get('difficulty', 'Medium')))}",
            styles["Normal"]
        ),
        Spacer(1, 18)
    ]

    for index, question in enumerate(questions, start=1):
        q_text = escape(str(question.get("question", "")))
        marks = int(question.get("marks", 0))

        story.append(Paragraph(
            f"<b>Q{index}. {q_text}</b> [{marks} marks]",
            styles["BodyText"]
        ))
        story.append(Spacer(1, 6))

        for option_index, option in enumerate(
            question.get("options", []), start=1
        ):
            story.append(Paragraph(
                f"{chr(64 + option_index)}. "
                f"{escape(str(option))}",
                styles["BodyText"]
            ))

        story.append(Spacer(1, 14))

    document.build(story)
    buffer.seek(0)

    filename = re.sub(
        r"[^a-zA-Z0-9_-]+", "_", subject
    ).strip("_") or "question_paper"

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{filename}.pdf"
    )


if __name__ == "__main__":
    try:
        init_db()
        print("MySQL connected. Database table is ready.")
    except mysql.connector.Error as exc:
        print("MySQL initialization failed:", exc)
        raise SystemExit(1)

    app.run(debug=True)
