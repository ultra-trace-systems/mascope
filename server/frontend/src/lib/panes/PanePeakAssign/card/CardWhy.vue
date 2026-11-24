<script setup>
import { reactive, watch } from 'vue'

import Popover from 'primevue/popover'

import { BaseTierTag } from '@/lib/base'
import { num } from '@/lib/formatters'
import { listingName, listingOf, listingTooltip } from '@/lib/referenceListings'
import { reasonFigure, reasonIcon, reasonTooltip } from '@/lib/tierReasons'
import { memberLabel, reasonTone } from './cardReadings.js'
import { useCard } from './useCard.js'

// Why the row holds its tier: the evidence drawn against the run's own bands,
// the product it is, and one line per reason - the rule's name with the number
// or formula it turned on, unfolding to the server's sentence. The reasons
// that hold the tier back come first, in the warning colour; those it kept its
// tier on follow in the support colour, a reason that spans rows listing them
// (docs/dev/assignment_ledger_and_card.md, section 4.1).
const {
  app,
  focusedAssignment,
  whyHeading,
  evidenceBar,
  productParts,
  productLine,
  evidenceText,
  whyReasons,
  whyOwnerReasons,
  reasonsOwner,
  neighbourPeak,
  focusNeighbour,
  groupOf,
  memberPeak,
  isOwnMember,
  focusMember,
  showGroupInLedger,
  plotGroup,
  corroboration,
  corroborationLabel,
  corroborationTooltip,
  otherReadings,
  readingTooltip,
  channelsMenu,
  toggleChannels,
  focusReading,
  sameIonReadings,
  SAME_ION_TOOLTIP,
  channelOf,
  EVIDENCE_TOOLTIPS
} = useCard()

// Which reasons are unfolded to their sentence, by list and index. A new row
// starts with every reason folded.
const opened = reactive(new Set())
const toggle = (key) => (opened.has(key) ? opened.delete(key) : opened.add(key))
watch(
  () => focusedAssignment.value?.peak_assignment_id,
  () => opened.clear()
)

const pct = (value) => `${Math.round(value)} %`
const memberTooltip = (member) =>
  isOwnMember(member)
    ? 'This peak'
    : memberPeak(member)
      ? `Focus this peak: ${member.formula ?? member.ion_formula ?? 'the row'}`
      : 'Not in this sample: the batch holds this row, this sample has no peak there'
</script>

<template>
  <section
    class="why"
    data-testid="why"
    v-help.right="{
      title: 'Why this tier',
      helpKey: 'assignment-tier-reasons',
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#why-a-row-holds-its-tier')
    }"
  >
    <h3 class="why-title" data-testid="why-title">{{ whyHeading }}</h3>
    <!-- The evidence the tier was read off, on a bar with the run's two bands
         marked: where it sits says which tier its evidence earned. -->
    <div v-if="evidenceBar" class="evidence-scale" data-testid="evidence-bar">
      <div
        class="bar"
        role="img"
        :aria-label="`Evidence ${evidenceText}; candidate from ${pct(evidenceBar.candidate)}, assigned from ${pct(evidenceBar.assigned)}`"
        v-tooltip.top="EVIDENCE_TOOLTIPS.evidence"
      >
        <span
          class="bar-fill"
          data-testid="evidence-fill"
          :style="{ width: `${evidenceBar.fill}%` }"
        />
        <span
          class="band-marker"
          data-testid="band-marker"
          data-band="candidate"
          :style="{ left: `${evidenceBar.candidate}%` }"
        />
        <span
          class="band-marker"
          data-testid="band-marker"
          data-band="assigned"
          :style="{ left: `${evidenceBar.assigned}%` }"
        />
      </div>
      <div class="band-labels">
        <span :style="{ left: `${evidenceBar.candidate}%` }">{{ pct(evidenceBar.candidate) }}</span>
        <span :style="{ left: `${evidenceBar.assigned}%` }">{{ pct(evidenceBar.assigned) }}</span>
      </div>
    </div>
    <div v-if="productLine" class="product-line" data-testid="product-line">
      <template v-for="(part, index) in productParts" :key="part.key"
        ><template v-if="index"> × </template
        ><span :data-part="part.key" v-tooltip.top="part.tooltip">{{ part.text }}</span></template
      ><template v-if="productParts.length === 2 && evidenceText"> = {{ evidenceText }}</template>
    </div>
    <div v-if="whyReasons.length || whyOwnerReasons.length" class="tier-reasons">
      <ul class="reasons">
        <template
          v-for="item in [
            ...whyReasons.map((reason, i) => ({ reason, key: `own-${i}`, viaM0: false })),
            ...whyOwnerReasons.map((reason, i) => ({ reason, key: `m0-${i}`, viaM0: true }))
          ]"
          :key="item.key"
        >
          <li
            :class="[
              'reason',
              `tone-${reasonTone(item.reason)}`,
              { inherited: item.viaM0, caps: reasonTone(item.reason) === 'holds-back' }
            ]"
            data-testid="reason"
            v-tooltip.left="
              reasonTooltip(
                item.reason,
                item.viaM0 ? reasonsOwner?.tier : focusedAssignment.tier,
                item.viaM0 ? { viaM0: true } : undefined
              )
            "
          >
            <button
              type="button"
              class="reason-head"
              data-testid="reason-toggle"
              :aria-expanded="opened.has(item.key) ? 'true' : 'false'"
              @click="toggle(item.key)"
            >
              <span :class="['pi', 'ph', reasonIcon(item.reason), 'reason-icon']" />
              <span class="reason-rule"
                >{{ item.reason.label }}<span v-if="item.viaM0" class="via"> via M0</span></span
              >
              <span
                v-if="reasonFigure(item.reason)"
                class="reason-figure"
                data-testid="reason-figure"
                >{{ reasonFigure(item.reason) }}</span
              >
            </button>
            <span v-show="opened.has(item.key)" class="reason-detail" data-testid="reason-detail">{{
              item.reason.detail
            }}</span>
            <template v-if="item.reason.neighbour">
              <button
                v-if="neighbourPeak(item.reason)"
                type="button"
                class="reason-neighbour"
                data-testid="reason-neighbour"
                :aria-label="`Focus the neighbouring peak at m/z ${num.mz.format(item.reason.neighbour.mz)}`"
                @click="focusNeighbour(item.reason)"
              >
                <span class="pi ph ph-crosshair" />
                neighbour at m/z {{ num.mz.format(item.reason.neighbour.mz) }}
              </button>
              <span v-else class="reason-neighbour stale" data-testid="reason-neighbour">
                neighbour at m/z {{ num.mz.format(item.reason.neighbour.mz) }}, no longer in the
                sample's peaks
              </span>
            </template>
            <!-- The rows a supporting reason spans, as the batch's group lists
                 them: each m/z focuses its peak where this sample holds one. -->
            <ul
              v-if="!item.viaM0 && groupOf(item.reason)"
              class="group-members"
              data-testid="group-members"
            >
              <li
                v-for="member in groupOf(item.reason).members"
                :key="member.batch_peak_id"
                v-tooltip.top="memberTooltip(member)"
              >
                <button
                  type="button"
                  class="member-link"
                  :class="{ own: isOwnMember(member) }"
                  data-testid="group-member"
                  :disabled="!memberPeak(member) || isOwnMember(member)"
                  @click="focusMember(member)"
                >
                  <span class="member-label">{{ memberLabel(member, groupOf(item.reason)) }}</span>
                  <span class="member-mz">m/z {{ num.mz.format(member.mz) }}</span>
                </button>
              </li>
            </ul>
            <!-- The group's rows together in the ledger, and its batch peaks
                 in the batch chart. -->
            <div
              v-if="!item.viaM0 && groupOf(item.reason)"
              class="group-actions"
              data-testid="group-actions"
            >
              <button
                type="button"
                class="group-action"
                data-testid="group-show"
                v-tooltip.top="'Narrow the ledger to these rows, in their order'"
                @click="showGroupInLedger(groupOf(item.reason))"
              >
                <span class="pi ph ph-funnel-simple" aria-hidden="true" />Show in the ledger
              </button>
              <button
                type="button"
                class="group-action"
                data-testid="group-plot"
                v-tooltip.top="
                  'Plot these batch peaks in the batch chart, in place of what it plots'
                "
                @click="plotGroup(groupOf(item.reason))"
              >
                <span class="pi ph ph-chart-line" aria-hidden="true" />Plot them
              </button>
            </div>
          </li>
        </template>
      </ul>
    </div>
    <!-- Seen through other channels of the run: a supporting line, which opens
         their peaks where the ledger holds them. -->
    <button
      v-if="corroboration && corroboration.n > 1 && otherReadings.length"
      type="button"
      class="corroboration opens"
      :class="{ inherited: corroboration.inherited }"
      data-testid="corroboration"
      aria-haspopup="dialog"
      v-tooltip.top="corroborationTooltip"
      @click="toggleChannels"
    >
      <span class="pi ph ph-link-simple" />
      {{ corroborationLabel }}
      <span class="pi ph ph-caret-down" />
    </button>
    <div
      v-else-if="corroboration && corroboration.n > 1"
      class="corroboration"
      :class="{ inherited: corroboration.inherited }"
      data-testid="corroboration"
      v-tooltip.top="corroborationTooltip"
    >
      <span class="pi ph ph-link-simple" />
      {{ corroborationLabel }}
    </div>
    <Popover ref="channelsMenu" aria-label="The other channels' peaks">
      <div class="other-readings">
        <div class="eyebrow">The same neutral, through the other channels</div>
        <ul class="readings">
          <li
            v-for="reading in otherReadings"
            :key="reading.row.peak_assignment_id"
            v-tooltip.left="readingTooltip(reading)"
          >
            <button
              type="button"
              class="reading-link"
              data-testid="other-reading"
              :disabled="!reading.peak"
              @click="focusReading(reading)"
            >
              <span class="reading-channel">{{ reading.channel ?? 'another channel' }}</span>
              <span class="reading-mz">m/z {{ num.mz.format(reading.row.sample_peak_mz) }}</span>
              <BaseTierTag :tier="reading.row.tier" :role="reading.row.role" />
            </button>
          </li>
        </ul>
      </div>
    </Popover>
    <!-- The splits of this ion the run did not commit, under the reasons that
         read them. -->
    <div v-if="sameIonReadings.length" class="same-ion" data-testid="same-ion">
      <div class="eyebrow">Same ion, read another way</div>
      <ul class="readings">
        <li
          v-for="(reading, i) in sameIonReadings"
          :key="`same-${i}`"
          class="reading"
          v-tooltip.left="SAME_ION_TOOLTIP"
        >
          <span class="pi ph ph-arrows-left-right reading-icon" />
          <span class="reading-formula">{{ reading.assigned_formula }}</span>
          <span v-if="channelOf(reading)" class="reading-channel">
            through {{ channelOf(reading) }}
          </span>
          <span
            v-if="listingOf(reading)"
            class="reading-listed"
            v-tooltip.left="listingTooltip(listingOf(reading))"
          >
            {{ listingName(listingOf(reading)) }}
          </span>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.why {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 6px;
}
.why-title {
  margin: 0;
  font-size: var(--card-text-base);
  font-weight: 700;
}
.evidence-scale {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.bar {
  position: relative;
  height: 8px;
  border-radius: 4px;
  background: var(--p-content-hover-background);
  cursor: help;
}
.bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 4px;
  background: var(--p-text-muted-color);
}
.band-marker {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 2px;
  margin-left: -1px;
  background: var(--p-text-color);
}
.band-labels {
  position: relative;
  height: 1.1em;
  color: var(--p-text-muted-color);
  font-family: var(--font-mono);
  font-size: var(--card-text-sm);
}
.band-labels span {
  position: absolute;
  transform: translateX(-50%);
  white-space: nowrap;
}
.product-line {
  font-family: var(--font-mono);
  font-size: var(--card-text-sm);
  font-variant-numeric: tabular-nums;
}
.reasons,
.readings,
.group-members {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.reason {
  display: flex;
  flex-direction: column;
  font-size: var(--card-text-sm);
  line-height: 1.35;
}
.reason-head {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.reason-head:hover .reason-rule,
.reason-head:focus-visible .reason-rule {
  text-decoration: underline;
}
.reason-icon {
  flex: none;
  color: var(--p-text-muted-color);
}
/* What a reason does, in the colour of what it does to the tier. */
.reason.tone-holds-back .reason-icon,
.reason.tone-holds-back .reason-rule {
  color: var(--state-warning);
}
.reason.tone-supports .reason-icon,
.reason.tone-supports .reason-rule {
  color: var(--state-success);
}
.reason-rule {
  font-weight: 500;
}
.reason-figure {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.reason-detail {
  padding-left: 1.2rem;
  color: var(--p-text-muted-color);
  overflow-wrap: anywhere;
}
.reason .via {
  color: var(--p-text-muted-color);
  font-weight: 400;
}
/* Read off the M0 rather than recorded on this peak: set in under a rule, as
   the words beside it say. Not dashed, which means not yet known. */
.reason.inherited {
  padding-left: 0.5rem;
  border-left: 1px solid var(--p-content-border-color);
}
.reason-neighbour,
.member-link {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  align-self: flex-start;
  padding: 0 0.3rem;
  margin-left: 0.9rem;
  border: none;
  border-radius: 0.25rem;
  background: none;
  color: var(--p-primary-color);
  font: inherit;
  font-variant-numeric: tabular-nums;
  text-align: left;
  cursor: pointer;
}
button.reason-neighbour:hover,
button.reason-neighbour:focus-visible,
.member-link:hover:not(:disabled),
.member-link:focus-visible {
  background: var(--p-content-hover-background);
}
.reason-neighbour.stale,
.member-link:disabled {
  color: var(--p-text-muted-color);
  cursor: default;
}
.member-link.own {
  font-weight: 700;
}
.member-label {
  min-width: 4.5rem;
}
/* Show in the ledger and Plot them, under the group's members. */
.group-actions {
  display: flex;
  gap: 0.4rem;
  margin-left: 0.9rem;
}
.group-action {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0 0.3rem;
  border: none;
  border-radius: 0.25rem;
  background: none;
  color: var(--p-primary-color);
  font: inherit;
  font-size: var(--card-text-sm);
  cursor: pointer;
}
.group-action:hover,
.group-action:focus-visible {
  background: var(--p-content-hover-background);
}
.member-mz {
  font-family: var(--font-mono);
}
.corroboration {
  align-self: start;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.1rem 0.5rem;
  border: 1px solid color-mix(in srgb, var(--state-success) 45%, transparent);
  border-radius: 100px;
  background: color-mix(in srgb, var(--state-success) 10%, transparent);
  color: var(--state-success);
  font: inherit;
  font-size: var(--card-text-sm);
  cursor: default;
}
.corroboration.opens {
  cursor: pointer;
}
.corroboration.opens:hover,
.corroboration.opens:focus-visible {
  background: color-mix(in srgb, var(--state-success) 20%, transparent);
}
.eyebrow {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  font-weight: 500;
}
.other-readings {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: var(--card-text-sm);
}
.reading-link {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.2rem 0.35rem;
  border: none;
  border-radius: 0.25rem;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.reading-link:hover:not(:disabled),
.reading-link:focus-visible {
  background: var(--p-content-hover-background);
}
.reading-link:disabled {
  cursor: default;
  opacity: 0.5;
}
.reading-mz,
.reading-formula {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.same-ion {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.reading {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
  font-size: var(--card-text-sm);
  cursor: default;
}
.reading-icon,
.reading-channel,
.reading-listed {
  color: var(--p-text-muted-color);
}
</style>
