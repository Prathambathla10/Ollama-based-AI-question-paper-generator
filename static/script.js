
const form = document.getElementById("paperForm");
const generateBtn = document.getElementById("generateBtn");
const statusMessage = document.getElementById("statusMessage");
const resultSection = document.getElementById("resultSection");
const questionsContainer = document.getElementById("questionsContainer");
const showAnswers = document.getElementById("showAnswers");
const downloadBtn = document.getElementById("downloadBtn");
const historyContainer = document.getElementById("historyContainer");

let currentPaper = null;

function showStatus(message, type = "") {
    statusMessage.textContent = message;
    statusMessage.className = type;
}

function makeElement(tag, className, text) {
    const element = document.createElement(tag);

    if (className) {
        element.className = className;
    }

    if (text !== undefined) {
        element.textContent = text;
    }

    return element;
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    currentPaper = null;
    resultSection.classList.add("hidden");
    generateBtn.disabled = true;
    generateBtn.textContent = "Generating...";
    showStatus(
        "Generating questions with your local AI model. This may take a few minutes."
    );

    try {
        const response = await fetch("/generate", {
            method: "POST",
            body: new FormData(form)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Generation failed.");
        }

        currentPaper = data;
        showAnswers.checked = false;
        renderPaper();
        resultSection.classList.remove("hidden");

        showStatus("Question paper generated and saved.", "success");
        await loadHistory();

    } catch (error) {
        showStatus(error.message, "error");
    } finally {
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate question paper";
    }
});

function renderPaper() {
    if (!currentPaper) return;

    document.getElementById("paperTitle").textContent =
        currentPaper.title || "Question Paper";

    document.getElementById("paperMeta").textContent =
        `${currentPaper.subject} · ${currentPaper.total_marks} marks · ` +
        `${currentPaper.difficulty} difficulty`;

    questionsContainer.replaceChildren();

    currentPaper.questions.forEach((question, index) => {
        const card = makeElement("article", "question-card");
        const top = makeElement("div", "question-top");

        top.append(
            makeElement("span", "", `QUESTION ${index + 1}`),
            makeElement(
                "span", "",
                `${question.marks} mark${question.marks === 1 ? "" : "s"}`
            )
        );

        card.appendChild(top);

        const typeLabel = makeElement(
            "p", "hint",
            `${question.type || "Question"} · ${question.topic || "General"}`
        );
        card.appendChild(typeLabel);

        const questionInput = makeElement("textarea");
        questionInput.setAttribute(
            "aria-label", `Edit question ${index + 1}`
        );
        questionInput.value = question.question;

        questionInput.addEventListener("input", () => {
            currentPaper.questions[index].question =
                questionInput.value;
        });

        card.appendChild(questionInput);

        if (question.options && question.options.length) {
            const optionsLabel = makeElement("p", "hint", "Options");
            card.appendChild(optionsLabel);

            question.options.forEach((option, optionIndex) => {
                const optionInput = makeElement("input", "option-input");
                optionInput.value = option;
                optionInput.setAttribute(
                    "aria-label",
                    `Question ${index + 1}, option ${optionIndex + 1}`
                );

                optionInput.addEventListener("input", () => {
                    currentPaper.questions[index].options[optionIndex] =
                        optionInput.value;
                });

                card.appendChild(optionInput);
            });
        }

        const answerBox = makeElement(
            "div", "answer-box",
            `Answer: ${question.answer || "No answer provided"}\n\n` +
            `Explanation: ${question.explanation || "No explanation provided"}`
        );

        if (!showAnswers.checked) {
            answerBox.classList.add("hidden");
        }

        card.appendChild(answerBox);
        questionsContainer.appendChild(card);
    });
}

showAnswers.addEventListener("change", () => {
    document.querySelectorAll(".answer-box").forEach((box) => {
        box.classList.toggle("hidden", !showAnswers.checked);
    });
});

downloadBtn.addEventListener("click", async () => {
    if (!currentPaper) return;

    downloadBtn.disabled = true;
    downloadBtn.textContent = "Preparing PDF...";

    try {
        const response = await fetch("/download", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(currentPaper)
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || "PDF export failed.");
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");

        link.href = url;
        link.download = "question_paper.pdf";
        document.body.appendChild(link);
        link.click();
        link.remove();

        URL.revokeObjectURL(url);
        showStatus("PDF downloaded successfully.", "success");

    } catch (error) {
        showStatus(error.message, "error");
    } finally {
        downloadBtn.disabled = false;
        downloadBtn.textContent = "Download PDF";
    }
});

async function loadHistory() {
    try {
        const response = await fetch("/history");
        const papers = await response.json();

        if (!response.ok) {
            throw new Error(papers.error || "History unavailable.");
        }

        historyContainer.replaceChildren();

        if (!papers.length) {
            historyContainer.appendChild(
                makeElement("p", "hint", "No saved papers yet.")
            );
            return;
        }

        papers.forEach((paper) => {
            const item = makeElement("div", "history-item");
            const details = makeElement("div");
            const title = makeElement("strong", "", paper.subject);
            const meta = makeElement(
                "p", "",
                `${paper.total_marks} marks · ${paper.difficulty} · ` +
                `${paper.created_at}`
            );

            details.append(title, meta);

            const openButton = makeElement(
                "button", "secondary", "Open"
            );
            openButton.type = "button";

            openButton.addEventListener("click", async () => {
                try {
                    const response = await fetch(`/paper/${paper.id}`);
                    const data = await response.json();

                    if (!response.ok) {
                        throw new Error(data.error || "Could not load paper.");
                    }

                    currentPaper = data;
                    currentPaper.id = paper.id;
                    showAnswers.checked = false;
                    renderPaper();
                    resultSection.classList.remove("hidden");
                    resultSection.scrollIntoView({ behavior: "smooth" });

                } catch (error) {
                    showStatus(error.message, "error");
                }
            });

            item.append(details, openButton);
            historyContainer.appendChild(item);
        });

    } catch (error) {
        historyContainer.replaceChildren(
            makeElement("p", "error", error.message)
        );
    }
}

document.getElementById("refreshHistory").addEventListener(
    "click", loadHistory
);

loadHistory();
