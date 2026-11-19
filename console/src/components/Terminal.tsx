import { useLayoutEffect, useRef, useState, type MouseEvent, type UIEvent } from "react";
import type { Line } from "../terminal";
import { collapsedPrompts, setPromptCollapsed } from "./promptFold";

// A terminal window: dark in both themes, scrolled to the newest line unless the reader scrolled up.
export function Terminal(props: { lines: Line[]; className?: string; onScrollTop?: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  const previousHeight = useRef(0);
  const previousFirst = useRef<number | null>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(collapsedPrompts);

  // The open state is the terminal's, not the browser's, so a collapsed prompt stays collapsed.
  function toggle(event: MouseEvent<HTMLElement>, position: number) {
    event.preventDefault();
    setCollapsed(setPromptCollapsed(position, !collapsed.has(position)));
  }

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
          // A line with more to it, such as a run's prompt, shows its full text until it is collapsed.
          <details key={line.position + line.kind} className={`t-${line.kind}`} open={!collapsed.has(line.position)}>
            <summary style={{ cursor: "pointer" }} onClick={(event) => toggle(event, line.position)}>{line.text}</summary>
            <pre style={{ margin: "2px 0 6px 16px", whiteSpace: "pre-wrap", font: "inherit" }}>{line.detail}</pre>
          </details>
        ),
      )}
    </div>
  );
}
