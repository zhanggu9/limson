"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Maximize2, Minimize2, MessageSquareText } from "lucide-react";
import { cn } from "@/lib/utils";

export interface DeckSlide {
  id: string;
  title: string;
  /** 1920×1080 기준으로 만든 슬라이드 한 장 (<section>…</section>) */
  html: string;
  /** 발표자 설명 */
  notes: string;
}

interface SlideDeckProps {
  title: string;
  slides: DeckSlide[];
}

const SLIDE_WIDTH = 1920;
const SLIDE_HEIGHT = 1080;
const SWIPE_MIN_PX = 48;

/** 슬라이드를 한 장씩 넘겨 보는 화면. 슬라이드는 고정 크기로 그리고 화면에 맞게 줄인다. */
export function SlideDeck({ title, slides }: SlideDeckProps) {
  const [index, setIndex] = useState(0);
  const [showNotes, setShowNotes] = useState(true);
  const [scale, setScale] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const touchStartX = useRef<number | null>(null);

  const last = slides.length - 1;
  const go = useCallback(
    (next: number) => setIndex(Math.max(0, Math.min(last, next))),
    [last]
  );

  // 슬라이드가 들어갈 칸의 크기에 맞춰 배율을 정한다
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setScale(Math.min(width / SLIDE_WIDTH, height / SLIDE_HEIGHT));
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (["ArrowRight", "PageDown", " "].includes(event.key)) {
        event.preventDefault();
        setIndex((i) => Math.min(last, i + 1));
      } else if (["ArrowLeft", "PageUp"].includes(event.key)) {
        event.preventDefault();
        setIndex((i) => Math.max(0, i - 1));
      } else if (event.key === "Home") {
        setIndex(0);
      } else if (event.key === "End") {
        setIndex(last);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last]);

  useEffect(() => {
    const onChange = () => setIsFullscreen(document.fullscreenElement === frameRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void frameRef.current?.requestFullscreen?.();
    }
  }, []);

  const slide = slides[index];
  if (!slide) return null;

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">{title}</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            LIMSON이 무엇이고 어떻게 돌아가는지 설명하는 슬라이드입니다. 키보드 ← → 로도 넘길 수 있습니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowNotes((v) => !v)}
            aria-pressed={showNotes}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors focus-ring",
              showNotes
                ? "bg-primary/10 text-primary"
                : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
            )}
          >
            <MessageSquareText className="w-4 h-4" aria-hidden="true" />
            설명 {showNotes ? "숨기기" : "보기"}
          </button>
          <button
            type="button"
            onClick={toggleFullscreen}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors focus-ring"
          >
            <Maximize2 className="w-4 h-4" aria-hidden="true" />
            전체 화면
          </button>
        </div>
      </div>

      {/* 전체 화면으로 바뀌는 틀 */}
      <div
        ref={frameRef}
        className={cn(
          "relative",
          isFullscreen ? "flex items-center justify-center bg-black" : "rounded-xl overflow-hidden shadow-lg border border-slate-200 dark:border-slate-700"
        )}
      >
        <div
          ref={stageRef}
          className={cn("relative overflow-hidden", isFullscreen ? "w-screen h-screen" : "w-full aspect-video")}
          onTouchStart={(event) => {
            touchStartX.current = event.touches[0]?.clientX ?? null;
          }}
          onTouchEnd={(event) => {
            const start = touchStartX.current;
            const end = event.changedTouches[0]?.clientX;
            touchStartX.current = null;
            if (start === null || end === undefined) return;
            if (end - start > SWIPE_MIN_PX) go(index - 1);
            else if (start - end > SWIPE_MIN_PX) go(index + 1);
          }}
        >
          <div
            key={slide.id}
            role="img"
            aria-label={`슬라이드 ${index + 1}: ${slide.title}`}
            className="limson-deck absolute left-1/2 top-1/2"
            style={{
              width: SLIDE_WIDTH,
              height: SLIDE_HEIGHT,
              transform: `translate(-50%, -50%) scale(${scale})`,
              visibility: scale > 0 ? "visible" : "hidden",
            }}
            // 슬라이드 내용은 저장소에 들어 있는 고정 데이터(src/data/limsonDeck.json)라서 그대로 넣는다
            dangerouslySetInnerHTML={{ __html: slide.html }}
          />
        </div>

        {isFullscreen && (
          <div className="absolute bottom-4 right-4 flex items-center gap-2 rounded-lg bg-black/60 px-2 py-1 text-white">
            <button type="button" onClick={() => go(index - 1)} disabled={index === 0} aria-label="이전 슬라이드" className="p-1.5 rounded disabled:opacity-30 hover:bg-white/20">
              <ChevronLeft className="w-5 h-5" aria-hidden="true" />
            </button>
            <span className="text-sm tabular-nums">{index + 1} / {slides.length}</span>
            <button type="button" onClick={() => go(index + 1)} disabled={index === last} aria-label="다음 슬라이드" className="p-1.5 rounded disabled:opacity-30 hover:bg-white/20">
              <ChevronRight className="w-5 h-5" aria-hidden="true" />
            </button>
            <button type="button" onClick={toggleFullscreen} aria-label="전체 화면 끝내기" className="p-1.5 rounded hover:bg-white/20">
              <Minimize2 className="w-5 h-5" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>

      {/* 넘기기 */}
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => go(index - 1)}
          disabled={index === 0}
          className="flex items-center gap-1 px-3 py-2 rounded-lg text-sm font-medium bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors focus-ring"
        >
          <ChevronLeft className="w-4 h-4" aria-hidden="true" />
          이전
        </button>
        <div className="flex flex-wrap items-center justify-center gap-1.5" role="tablist" aria-label="슬라이드 목록">
          {slides.map((item, i) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={`${i + 1}번 슬라이드: ${item.title}`}
              title={item.title}
              onClick={() => go(i)}
              className={cn(
                "w-8 h-8 rounded-full text-sm font-medium transition-colors focus-ring",
                i === index
                  ? "bg-primary text-white"
                  : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700"
              )}
            >
              {i + 1}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => go(index + 1)}
          disabled={index === last}
          className="flex items-center gap-1 px-3 py-2 rounded-lg text-sm font-medium bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors focus-ring"
        >
          다음
          <ChevronRight className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>

      {showNotes && (
        <section className="card p-4 space-y-2" aria-label="슬라이드 설명">
          <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
            {index + 1}. {slide.title}
          </h2>
          <p className="text-sm leading-7 text-slate-600 dark:text-slate-400 whitespace-pre-line">
            {slide.notes || "이 슬라이드에는 설명이 없습니다."}
          </p>
        </section>
      )}
    </div>
  );
}

export default SlideDeck;
