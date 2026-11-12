import type { Metadata } from "next";
import { SlideDeck } from "@/components/slides/SlideDeck";
import deck from "@/data/limsonDeck.json";

export const metadata: Metadata = {
  title: "LIMSON 소개 슬라이드",
  description: "LIMSON이 무엇이고 어떻게 돌아가는지 설명하는 슬라이드",
};

export default function SlidesPage() {
  return <SlideDeck title="LIMSON 소개 슬라이드" slides={deck.slides} />;
}
