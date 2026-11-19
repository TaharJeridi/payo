import { keepPreviousData, useMutation, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useEffect } from "react";

import { useAuth } from "../auth/AuthContext";
import { ApiError } from "./client";
import type { PaymentStatus } from "./types";

function useLogoutOnUnauthorized(result: { error: unknown }): void {
  const { logout } = useAuth();
  useEffect(() => {
    if (result.error instanceof ApiError && result.error.isUnauthorized) logout();
  }, [result.error, logout]);
}

function useGuarded<T>(result: UseQueryResult<T, Error>): UseQueryResult<T, Error> {
  useLogoutOnUnauthorized(result);
  return result;
}

const retry = (count: number, error: Error) =>
  !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2;

export function useStats() {
  const { client } = useAuth();
  return useGuarded(useQuery({ queryKey: ["stats"], queryFn: () => client.stats(), retry }));
}

export function usePayments(params: { status: PaymentStatus | undefined; limit: number; offset: number }) {
  const { client } = useAuth();
  return useGuarded(
    useQuery({
      queryKey: ["payments", params],
      queryFn: () => client.payments(params),
      placeholderData: keepPreviousData,
      retry,
    }),
  );
}

export function usePayment(id: string) {
  const { client } = useAuth();
  return useGuarded(useQuery({ queryKey: ["payment", id], queryFn: () => client.payment(id), retry }));
}

export function useProducts() {
  const { baseClient } = useAuth();
  return useQuery({ queryKey: ["products"], queryFn: () => baseClient.products(), staleTime: 300_000 });
}

export function useApiHealth() {
  const { baseClient } = useAuth();
  return useQuery({ queryKey: ["health"], queryFn: () => baseClient.health(), refetchInterval: 30_000, retry: false });
}

export function useRagHealth() {
  const { baseClient } = useAuth();
  return useQuery({
    queryKey: ["rag-health"],
    queryFn: () => baseClient.ragHealth(),
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useAsk() {
  const { client, logout } = useAuth();
  return useMutation({
    mutationFn: (question: string) => client.ask(question),
    onError: (error) => {
      if (error instanceof ApiError && error.isUnauthorized) logout();
    },
  });
}
