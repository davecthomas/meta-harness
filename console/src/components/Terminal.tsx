import { useLayoutEffect, useRef, type UIEvent } from "react";
import type { Line } from "../terminal";

// A terminal window: dark in both themes, scrolled to the newest line unless the reader scrolled up.
export function Terminal(props: { lines: Line[]; className?: string; onScrollTop?: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  const previousHeight = useRef(0);
  const previousFirst = useRef<number | null>(null);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const first = props.lines[0]?.position ?? null;
    if (previousFirst.current !== null && first !== null && first < previousFirst.current) {
      // Older lines were added above: keep the reader's place.
      node.scrollTop += node.scrollHeight - previousHeight.current;
    } else if (pinned.current) {
      node.scrollTop = node.scrollHeight;
    }
    previousHeight.current = node.scrollHeight;
    previousFirst.current = first;
  }, [props.lines]);

  function onScroll(event: UIEvent<HTMLDivElement>) {
    const node = event.currentTarget;
    pinned.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 8;
    if (node.scrollTop < 24 && props.onScrollTop) props.onScrollTop();
  }

  return (
    <div ref={ref} className={`terminal ${props.className ?? ""}`} onScroll={onScroll} role="log" aria-live="off">
      {props.lines.map((line) =>
        line.detail === undefined ? (
          <div key={line.position + line.kind} className={`t-${line.kind}`}>{line.text}</div>
        ) : (
          // A line with more to it, such as a run's prompt, opens to the full text.
          <details key={line.position + line.kind} className={`t-${line.kind}`}>
            <summary style={{ cursor: "pointer" }}>{line.text}</summary>
            <pre style={{ margin: "2px 0 6px 16px", whiteSpace: "pre-wrap", font: "inherit" }}>{line.detail}</pre>
          </details>
        ),
      )}
    </div>
  );
}
