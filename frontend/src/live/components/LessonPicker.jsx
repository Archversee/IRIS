// A plain dropdown over MAVIS's flat state.lesson_list (mirrors
// state.lesson_catalog's modules, just flattened) -- kept minimal rather
// than porting the original app's full accordion sidebar widget.
export default function LessonPicker({ lessonList, activeLessonId, onSelect }) {
  if (!lessonList?.length) {
    return <p className="muted">Loading lessons…</p>;
  }
  return (
    <div>
      <label htmlFor="mavis-lesson-select">Lesson</label>
      <select
        id="mavis-lesson-select"
        value={activeLessonId || ""}
        onChange={(e) => {
          const lesson = lessonList.find((l) => l.id === e.target.value);
          if (lesson) onSelect(lesson);
        }}
      >
        {lessonList.map((les) => (
          <option key={les.id} value={les.id} disabled={les.available === false}>
            {les.title}
            {les.kind === "reading" ? " (reading)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
