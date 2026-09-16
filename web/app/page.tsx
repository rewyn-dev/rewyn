"use client";

import { Console } from "@/components/common/Console";
import { RouterProvider } from "@/lib/router";

export default function Page() {
  return (
    <RouterProvider>
      <Console />
    </RouterProvider>
  );
}
