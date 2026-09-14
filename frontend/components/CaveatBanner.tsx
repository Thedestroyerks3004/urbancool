import { AlertTriangle } from "lucide-react";

interface CaveatBannerProps {
  caveat: string;
}

// Always-visible: rendered once at the app root, sourced from the API's own `caveats`
// field (never hardcoded here) so backend and frontend can never drift apart on wording.
export default function CaveatBanner({ caveat }: CaveatBannerProps) {
  return (
    <div className="flex items-center gap-2 px-6 py-2 bg-[#3a3227] border-t border-[#2a241c] z-30">
      <AlertTriangle size={15} className="text-[#e8c46a] shrink-0" />
      <span className="text-xs text-[#e9e4d8] leading-snug">{caveat}</span>
    </div>
  );
}
