import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getPage, post } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { Schemas } from "@/api/types";

/**
 * What a landlord or caretaker does with the messages and records that arrive.
 *
 * The same list endpoints as the student side: the API scopes by who is
 * asking, and a client reaching for "my inquiries" and "inquiries about my
 * properties" through different URLs would be two places for one
 * authorization rule.
 */

export function useIncomingInquiries(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.inquiries.list(),
    queryFn: () => getPage<Schemas["Inquiry"]>("/engagement/inquiries/"),
    enabled,
  });
}

export function useRespondToInquiry() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ id, response }: { id: number; response: string }) =>
      post<Schemas["Inquiry"]>(`/engagement/inquiries/${id}/respond/`, { response }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.inquiries.all() }),
  });
}

export function useCloseInquiry() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => post<Schemas["Inquiry"]>(`/engagement/inquiries/${id}/close/`),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.inquiries.all() }),
  });
}

export function useIncomingApplications(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.applications.list(),
    queryFn: () => getPage<Schemas["Application"]>("/tenancies/applications/"),
    enabled,
  });
}

export function useDecideApplication(decision: "accept" | "reject") {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ id, note }: { id: number; note: string }) =>
      post(`/tenancies/applications/${id}/${decision}/`, { note }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.applications.all() });
      // Accepting creates a confirmed tenancy directly (ADR-004 §1.1), so the
      // tenancy lists are stale the moment this returns.
      client.invalidateQueries({ queryKey: queryKeys.tenancies.all() });
    },
  });
}

export function useIncomingClaims(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.claims.list(),
    queryFn: () => getPage<Schemas["TenancyClaim"]>("/tenancies/claims/"),
    enabled,
  });
}

export function useConfirmClaim() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => post(`/tenancies/claims/${id}/confirm/`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.claims.all() });
      client.invalidateQueries({ queryKey: queryKeys.tenancies.all() });
    },
  });
}

export function useDisputeClaim() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ id, ...body }: { id: number } & Schemas["DisputeRequest"]) =>
      post<Schemas["TenancyClaim"]>(`/tenancies/claims/${id}/dispute/`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.claims.all() }),
  });
}
