import type { Metadata } from "next";
import { Barlow, Barlow_Condensed } from "next/font/google";
import "./globals.css";

// Barlow: a grotesk drawn from highway and site signage. Condensed cut for the
// site board, headings and figures; regular cut for reading text.
const barlow = Barlow({ variable: "--font-barlow", subsets: ["latin"], weight: ["400", "500", "600"] });
const barlowCondensed = Barlow_Condensed({ variable: "--font-barlow-condensed", subsets: ["latin"], weight: ["500", "600", "700"] });

export const metadata: Metadata = {
  title: "Site Manager",
  description: "Your AI project manager: progress, money and decisions for every site",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${barlow.variable} ${barlowCondensed.variable} antialiased`}>{children}</body>
    </html>
  );
}
